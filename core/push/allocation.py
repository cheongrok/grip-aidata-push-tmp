"""다중 푸시 분배 — 클러스터적용 노트북 §2.5 allocate_pushes 의 확장판.

확장점:
- 푸시별 (카테고리, 시간대) 조건부 CTR: (cat×hour, n>=min_n) → (cat, n>=min_n) → 전체 폴백 체인
- 클러스터 크기를 외부 주입 (latest_cluster.csv 스냅샷 — 5월 reach 분포 대신 현재 모수)
- 카테고리 중복 허용 (푸시는 index 로 식별)
- 전체발송 대비 효율 지표 (기대 오픈수·오픈율·클릭 커버리지·발송 절감)

원칙은 §2.5 와 동일: 누구에게 = 활성도(MAY_ORDER 상위 top_k), 무엇을 = 비교우위(메인효과 제거) + 수송 LP.
"""

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from core.push import artifacts
from core.push.descriptions import SHORT_NAME


def _ctr_chain(stats: pd.DataFrame, base: pd.Series, cid: int, category: str, hour_bucket: str | None, min_n: int):
    """(cat×hour) → (cat) → 전체 베이스 폴백 체인. (ctr, fallback_level) 반환 — 0=조건부, 1=카테고리만, 2=전체."""
    if hour_bucket is not None:
        s = stats[(stats["PUSH_CAT"] == category) & (stats["PUSH_HOUR_BUCKET"] == hour_bucket)]
        g = s.groupby("CLUSTER")[["n", "opens"]].sum()
        if cid in g.index and g.loc[cid, "n"] >= min_n:
            return float(g.loc[cid, "opens"] / g.loc[cid, "n"]), 0
    s = stats[stats["PUSH_CAT"] == category]
    g = s.groupby("CLUSTER")[["n", "opens"]].sum()
    if cid in g.index and g.loc[cid, "n"] >= min_n:
        return float(g.loc[cid, "opens"] / g.loc[cid, "n"]), 1
    return float(base.loc[cid, "opens"] / base.loc[cid, "n"]), 2


def _round_preserve_row_sums(X: np.ndarray, row_sums: np.ndarray) -> np.ndarray:
    """행합을 정확히 보존하는 정수 반올림 (largest remainder).

    LP 해 X 는 행합 = 클러스터 인원(row_sums)을 만족하지만 실수다. 셀별 단순 반올림은
    행합이 어긋나 CSV 슬라이싱에서 유저 누락/초과를 만들므로, floor 후 잔여분을
    소수부 큰 셀부터 배분해 행합을 정수로 정확히 맞춘다.
    """
    Xr = np.floor(X + 1e-9).astype(int)
    for i in range(X.shape[0]):
        deficit = int(round(row_sums[i])) - int(Xr[i].sum())
        if deficit > 0:
            frac = X[i] - Xr[i]
            for j in np.argsort(-frac)[:deficit]:
                Xr[i, j] += 1
    return Xr


def _cat_eff(stats: pd.DataFrame, category: str, hour_bucket: str | None, min_n: int) -> float:
    """푸시(카테고리×시간대)의 전체 평균 CTR — 비교우위 정규화용 (동일 폴백 체인의 집계 버전)."""
    if hour_bucket is not None:
        s = stats[(stats["PUSH_CAT"] == category) & (stats["PUSH_HOUR_BUCKET"] == hour_bucket)]
        if s["n"].sum() >= min_n:
            return float(s["opens"].sum() / s["n"].sum())
    s = stats[stats["PUSH_CAT"] == category]
    if s["n"].sum() >= min_n:
        return float(s["opens"].sum() / s["n"].sum())
    return float(stats["opens"].sum() / stats["n"].sum())


def estimate_ctr(pushes: list[dict], clusters: list[int], min_n: int = 2000):
    """클러스터 × 푸시 기대 CTR 행렬 + 폴백 레벨 행렬. pushes[i] = {category, hour_bucket}."""
    art = artifacts.load()
    stats, base = art["STATS"], art["BASE"]
    est = np.zeros((len(clusters), len(pushes)))
    fb = np.zeros((len(clusters), len(pushes)), dtype=int)
    for i, cid in enumerate(clusters):
        for j, p in enumerate(pushes):
            est[i, j], fb[i, j] = _ctr_chain(stats, base, cid, p["category"], p.get("hour_bucket"), min_n)
    return est, fb


def allocate(
    pushes: list[dict],
    cluster_sizes: dict[int, int],
    top_k: int = 5,
    volumes: list[float] | None = None,
    use_interaction: bool = True,
    min_n: int = 2000,
    delivery_rate: float = 1.0,
) -> dict:
    """푸시 N개에 클러스터(유저)를 상호배타 분배하고 효율 지표를 계산한다.

    pushes        : [{"title": str, "category": str, "hour_bucket": str|None, "clusters": [int]?}], 1개 이상
                    push 에 "clusters"(원본 cluster id)가 있으면 그 푸시는 해당 클러스터에만 발송(수동 모드).
                    하나라도 clusters 가 있으면 전체 수동 모드 — 모든 푸시가 clusters 를 가져야 함.
    cluster_sizes : {cluster_id: 유저수} — latest 스냅샷 기준 (전체 10개 모두 필요)
    top_k         : 발송 풀 = MAY_ORDER 상위 k개 (5=Tier1+2 검증값)
    volumes       : 푸시별 발송 비중(합 1). None=균등
    delivery_rate : 도달률(reached/sent). 모델 CTR 은 도달 대비라 발송 대상 수에 그대로 곱하면
                    오픈이 과대(=1/도달률 배). 이 값을 곱해 '발송 대비' 기대오픈으로 환산한다.
                    1.0=환산 안 함(도달 대비 그대로). 순위·배정·커버리지%·배율은 비율이라 불변.
    반환          : {"matrix": {cluster: {push_idx: count}}, "per_push": [...], "totals": {...}}
    """
    assert len(pushes) >= 1
    art = artifacts.load()
    stats, may_order, desc = art["STATS"], art["MAY_ORDER"], art["DESC"]
    nk = len(pushes)

    all_clusters = may_order  # 전체 10개 (전체발송 시나리오용)
    n_all = np.array([float(cluster_sizes[c]) for c in all_clusters])
    total_users = float(n_all.sum())

    # 수동 타겟 모드: 푸시별로 보낼 클러스터를 직접 지정(pushes[j]["clusters"]). 하나라도 있으면 수동.
    clusters_sets = [{int(c) for c in (p.get("clusters") or [])} for p in pushes]
    manual = any(clusters_sets)

    if manual:
        if not all(clusters_sets):
            raise ValueError("수동 모드에서는 모든 푸시에 세그먼트를 1개 이상 선택해야 해요.")
        union = set().union(*clusters_sets)
        pool = [c for c in may_order if c in union]  # 선택된 클러스터만 (발송 우선순위 순)
        n_pool = np.array([float(cluster_sizes[c]) for c in pool])
        est_pool, fb_pool = estimate_ctr(pushes, pool, min_n)
        # 각 클러스터 유저를 그 클러스터를 선택한 푸시들에 균등 분배 (largest-remainder 정수)
        Xr = np.zeros((len(pool), nk), dtype=int)
        for i, c in enumerate(pool):
            selecting = [j for j in range(nk) if c in clusters_sets[j]]
            users_c = int(round(cluster_sizes[c]))
            if not selecting or users_c <= 0:
                continue
            base, rem = divmod(users_c, len(selecting))
            for r_idx, j in enumerate(selecting):
                Xr[i, j] = base + (1 if r_idx < rem else 0)
    else:
        fr = np.full(nk, 1 / nk) if volumes is None else np.asarray(volumes, float)
        assert len(fr) == nk and abs(fr.sum() - 1) < 1e-9, "volumes 는 푸시 수와 같고 합=1 이어야 함"
        pool = may_order[:top_k]
        n_pool = np.array([float(cluster_sizes[c]) for c in pool])
        est_pool, fb_pool = estimate_ctr(pushes, pool, min_n)

        # ── 배정 score: 비교우위(카테고리 메인효과 제거) — 단일 푸시면 분배가 자명하므로 LP 생략 ──
        if nk == 1:
            X = n_pool.reshape(-1, 1).copy()
        else:
            if use_interaction:
                eff = np.array([_cat_eff(stats, p["category"], p.get("hour_bucket"), min_n) for p in pushes])
                score = est_pool / eff
            else:
                score = est_pool
            nc = len(pool)
            A_eq, b_eq = [], []
            for i in range(nc):  # 클러스터 전량 배정
                r = np.zeros(nc * nk)
                r[i * nk : (i + 1) * nk] = 1
                A_eq.append(r)
                b_eq.append(n_pool[i])
            for k in range(nk):  # 푸시별 모수 제약
                r = np.zeros(nc * nk)
                r[k::nk] = 1
                A_eq.append(r)
                b_eq.append(n_pool.sum() * fr[k])
            res = linprog(-score.flatten(), A_eq=np.array(A_eq), b_eq=np.array(b_eq), bounds=(0, None), method="highs")
            assert res.success, f"LP 실패: {res.message}"
            X = res.x.reshape(nc, nk)

        # 정수화: 행합(클러스터 인원) 정확 보존 (유저 누락 방지)
        Xr = _round_preserve_row_sums(X, n_pool)

    est_all, _ = estimate_ctr(pushes, all_clusters, min_n)

    # ── 푸시별 지표: 타겟 발송 vs 전체발송 시나리오 (기대 오픈은 항상 raw CTR 로 평가) ──
    per_push = []
    for j, p in enumerate(pushes):
        sends = float(Xr[:, j].sum())
        # 발송 대비 환산: 도달 대비 CTR × 도달률 (delivery_rate=1.0 이면 도달 대비 그대로)
        opens_t = float((Xr[:, j] * est_pool[:, j]).sum()) * delivery_rate
        opens_full = float((n_all * est_all[:, j]).sum()) * delivery_rate
        rate_t = opens_t / sends * 100 if sends else 0.0
        rate_full = opens_full / total_users * 100
        per_push.append(
            {
                "title": p.get("title") or f"push_{j + 1}",
                "category": p["category"],
                "hour_bucket": p.get("hour_bucket"),
                "target_sends": int(sends),
                "expected_opens": int(round(opens_t)),
                "expected_open_rate_pct": round(rate_t, 3),
                "full_sends": int(total_users),
                "full_expected_opens": int(round(opens_full)),
                "full_open_rate_pct": round(rate_full, 3),
                "click_coverage_pct": round(opens_t / opens_full * 100, 1) if opens_full else None,
                "send_reduction_pct": round((1 - sends / total_users) * 100, 1),
                "ctr_multiplier": round((rate_t / rate_full), 2) if rate_full else None,
            }
        )

    # 셀 포함 기준 = 정수화 후 1명 이상 (count>=1 이면 n_pool[i]>0 이 보장되어 share 나눗셈 안전)
    rank_of = art["RANK"]
    matrix = []
    for i, cid in enumerate(pool):
        row = {
            "cluster": int(cid),
            "rank": rank_of[int(cid)],
            "short_name": SHORT_NAME.get(int(cid), ""),
            "desc": desc.get(cid, ""),
            "size": int(cluster_sizes[cid]),
            "allocation": {
                j: {"count": int(Xr[i, j]), "share_pct": round(Xr[i, j] / n_pool[i] * 100, 1),
                    "expected_ctr_pct": round(est_pool[i, j] * delivery_rate * 100, 3),
                    "fallback_level": int(fb_pool[i, j])}
                for j in range(nk)
                if Xr[i, j] >= 1
            },
        }
        matrix.append(row)

    opens_t_total = sum(r["expected_opens"] for r in per_push)
    opens_full_total = sum(r["full_expected_opens"] for r in per_push)
    sends_total = sum(r["target_sends"] for r in per_push)
    return {
        "pool": [int(c) for c in pool],
        "matrix": matrix,
        "per_push": per_push,
        "totals": {
            "total_users": int(total_users),
            "target_sends": int(sends_total),
            "send_pct": round(sends_total / total_users * 100, 1),
            "expected_opens": int(opens_t_total),
            "full_expected_opens": int(opens_full_total),
            "click_coverage_pct": round(opens_t_total / opens_full_total * 100, 1) if opens_full_total else None,
        },
    }
