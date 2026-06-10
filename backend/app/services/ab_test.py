"""푸시알림 효과검증 (A/B 홀드아웃) — 발송군 vs 대조군의 매핑 방송 시청·구매 증분.

분배 페이지 홀드아웃으로 만든 발송/대조군 CSV(또는 CRM 발송 리스트)의 user_seq 를 받아,
content_seq 의 발송 이후 유효시청·구매·GMV 를 각 그룹에서 집계하고
증분(lift)·2-비율 z검정·세그먼트별 분해를 낸다.

측정 원칙(ITT): 발송군 '전체' vs 대조군 '전체' 비교(오픈자만 거르지 않음).
대조군은 푸시를 안 받았으니 오픈/클릭이 아니라 '둘 다 할 수 있는 행동'(시청·구매·GMV)만 비교한다.
push_at·content_seq·user_seq 는 모두 정수/DB값 → f-string 주입 위험 없음.
"""

import math
from datetime import timedelta

import pandas as pd

from core.datasources.snowflake_kp import run_query
from core.push import artifacts
from core.push.descriptions import SHORT_NAME

UNASSIGNED = -1


def _push_at(push_seq: int) -> str | None:
    """발송시각 = push_send_history MIN(push_at) (send_status='Y') — 다른 집계와 동일 기준."""
    df = run_query(
        "SELECT MIN(push_at) AS push_at FROM data_anal.push_send_history "
        f"WHERE send_status='Y' AND push_seq={int(push_seq)}"
    )
    df.columns = df.columns.str.lower()
    v = df.iloc[0]["push_at"] if len(df) else None
    return pd.Timestamp(v).strftime("%Y-%m-%d %H:%M:%S") if pd.notna(v) else None


def _now() -> str:
    return pd.Timestamp(
        run_query("SELECT CURRENT_TIMESTAMP()::timestamp_ntz AS T").iloc[0]["T"]
    ).strftime("%Y-%m-%d %H:%M:%S")


def _outcome_sql(content_seqs: list[int], t_start: str, t_end: str) -> str:
    """[t_start, t_end) 안에 content_seq 를 유효시청(≥10s)/구매(취소제외·gmv>0)한 유저 단위 결과."""
    cs = ", ".join(str(int(c)) for c in content_seqs)
    return f"""
    WITH w AS (
        SELECT es.userseq AS user_seq, 1 AS viewed
        FROM   default.elasticsearch es
        WHERE  es.contentseq IN ({cs})
          AND  es.viewtimemillis >= 10000
          AND  es."@timestamp" >= TIMESTAMP '{t_start}' AND es."@timestamp" < TIMESTAMP '{t_end}'
        GROUP BY es.userseq
    ),
    b AS (
        SELECT ord.user_seq, SUM(ord.gmv) AS gmv
        FROM   default.order_all ord
        WHERE  ord.content_seq IN ({cs})
          AND  ord.cancel_at IS NULL AND ord.gmv > 0
          AND  ord.ordered_at >= TIMESTAMP '{t_start}' AND ord.ordered_at < TIMESTAMP '{t_end}'
        GROUP BY ord.user_seq
    )
    SELECT COALESCE(w.user_seq, b.user_seq) AS user_seq,
           IFF(w.user_seq IS NOT NULL, 1, 0) AS viewed,
           IFF(b.user_seq IS NOT NULL, 1, 0) AS purchased,
           COALESCE(b.gmv, 0)                AS gmv
    FROM   w FULL OUTER JOIN b ON w.user_seq = b.user_seq
    """


def _ztest(x1: int, n1: int, x2: int, n2: int):
    """2-비율 z검정 (양측). (z, p) 반환, 표본 0이면 (None, None)."""
    if not n1 or not n2:
        return None, None
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    return round(z, 3), round(math.erfc(abs(z) / math.sqrt(2)), 4)


def _arm_df(user_seqs: list[int], clusters: list[int], outcomes: pd.DataFrame) -> pd.DataFrame:
    """그룹 user_seq(+cluster) 를 결과(outcomes)에 left-join — 미시청/미구매는 0."""
    if not user_seqs:
        return pd.DataFrame(columns=["user_seq", "cluster", "viewed", "purchased", "gmv"])
    g = pd.DataFrame({"user_seq": pd.Series(user_seqs, dtype="int64")})
    g["cluster"] = (
        pd.Series(clusters, dtype="int64") if clusters and len(clusters) == len(user_seqs)
        else UNASSIGNED
    )
    g = g.drop_duplicates("user_seq")
    m = g.merge(outcomes, on="user_seq", how="left")
    m["viewed"] = m["viewed"].fillna(0).astype(int)
    m["purchased"] = m["purchased"].fillna(0).astype(int)
    m["gmv"] = pd.to_numeric(m["gmv"], errors="coerce").fillna(0.0)
    return m


def _arm_stats(m: pd.DataFrame) -> dict:
    n = len(m)
    w, b, gmv = int(m["viewed"].sum()), int(m["purchased"].sum()), float(m["gmv"].sum())
    return {
        "n_users": n, "n_watch": w, "n_purchase": b, "gmv_sum": int(round(gmv)),
        "watch_rate_pct": round(w / n * 100, 3) if n else 0.0,
        "purchase_rate_pct": round(b / n * 100, 3) if n else 0.0,
        "gmv_per_user": int(round(gmv / n)) if n else 0,
        "aov": int(round(gmv / b)) if b else 0,
    }


def verify(
    push_seq: int, content_seqs: list[int],
    t_user_seqs: list[int], t_clusters: list[int],
    c_user_seqs: list[int], c_clusters: list[int],
    days: int | None = None,
) -> dict:
    push_seq = int(push_seq)
    content_seqs = sorted({int(c) for c in content_seqs})
    if not content_seqs:
        raise ValueError("content_seq 를 1개 이상 입력하세요.")
    if not t_user_seqs or not c_user_seqs:
        raise ValueError("발송군과 대조군 CSV 가 모두 필요합니다.")

    t_start = _push_at(push_seq)
    if t_start is None:
        raise ValueError(f"push_seq {push_seq} 의 발송 기록(send_status=Y)이 없습니다 — 발송된 푸시인지 확인하세요.")
    t_end = (
        (pd.Timestamp(t_start) + timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
        if days else _now()
    )

    out = run_query(_outcome_sql(content_seqs, t_start, t_end))
    out.columns = out.columns.str.lower()
    out = out if len(out) else pd.DataFrame(columns=["user_seq", "viewed", "purchased", "gmv"])
    if len(out):
        out["user_seq"] = out["user_seq"].astype("int64")

    tm = _arm_df(t_user_seqs, t_clusters, out)
    cm = _arm_df(c_user_seqs, c_clusters, out)

    warnings: list[str] = []
    # 두 그룹에 겹치는 유저는 모호 → 양쪽에서 제외 (오염 방지)
    overlap = set(tm["user_seq"]) & set(cm["user_seq"])
    if overlap:
        tm = tm[~tm["user_seq"].isin(overlap)]
        cm = cm[~cm["user_seq"].isin(overlap)]
        warnings.append(f"발송군·대조군에 겹치는 유저 {len(overlap):,}명 — 두 그룹에서 제외하고 계산했습니다.")

    T, C = _arm_stats(tm), _arm_stats(cm)
    if T["n_purchase"] + C["n_purchase"] < 20:
        warnings.append("구매 표본이 작아(<20) 구매 증분은 통계적으로 불안정합니다 — 유효시청 지표·복수 푸시 누적을 권장합니다.")
    if days is None:
        warnings.append("측정 종료를 '현재'로 잡았습니다 — 발송 직후라면 뒤늦은 구매가 덜 반영될 수 있습니다(며칠 뒤 재실행 권장).")

    def metric(key_rate: str, t_count: int, c_count: int) -> dict:
        z, pv = _ztest(t_count, T["n_users"], c_count, C["n_users"])
        return {
            "treatment_pct": T[key_rate], "control_pct": C[key_rate],
            "lift_pp": round(T[key_rate] - C[key_rate], 3),
            "z": z, "p_value": pv, "significant": bool(pv is not None and pv < 0.05),
        }

    watch = metric("watch_rate_pct", T["n_watch"], C["n_watch"])
    purchase = metric("purchase_rate_pct", T["n_purchase"], C["n_purchase"])

    # 세그먼트별 (cluster 정보가 실제로 있을 때만 — 전부 미배정이면 생략)
    rank = artifacts.load()["RANK"]
    by_cluster: list[dict] = []
    real_clusters = (set(tm["cluster"]) | set(cm["cluster"])) - {UNASSIGNED}
    if real_clusters:
        for cid in sorted(set(tm["cluster"]) | set(cm["cluster"])):
            t = tm[tm["cluster"] == cid]
            c = cm[cm["cluster"] == cid]
            if not len(t) and not len(c):
                continue
            tr = t["purchased"].mean() * 100 if len(t) else 0.0
            cr = c["purchased"].mean() * 100 if len(c) else 0.0
            _, pv = _ztest(int(t["purchased"].sum()), len(t), int(c["purchased"].sum()), len(c))
            by_cluster.append({
                "cluster": int(cid),
                "rank": rank.get(int(cid)),
                "short_name": SHORT_NAME.get(int(cid), ""),
                "t_users": len(t), "c_users": len(c),
                "t_purchase_rate_pct": round(tr, 3), "c_purchase_rate_pct": round(cr, 3),
                "lift_pp": round(tr - cr, 3), "p_value": pv,
            })
        by_cluster.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0))

    return {
        "push_seq": push_seq, "push_at": t_start,
        "content_seqs": content_seqs,
        "period_start": t_start, "period_end": t_end,
        "treatment": T, "control": C,
        "watch": watch, "purchase": purchase,
        "gmv_per_user_treatment": T["gmv_per_user"],
        "gmv_per_user_control": C["gmv_per_user"],
        "gmv_per_user_lift": T["gmv_per_user"] - C["gmv_per_user"],
        "by_cluster": by_cluster,
        "warnings": warnings,
    }
