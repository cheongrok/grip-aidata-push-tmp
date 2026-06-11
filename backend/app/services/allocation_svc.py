"""다중 푸시 분배 + 푸시별 user_id CSV 생성 (페이지 ③)."""

import json
import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from app.services.snapshot import read_meta
from core.datasources.snowflake_kp import run_query
from core.push import artifacts
from core.push.allocation import allocate
from core.push.paths import ALLOCATIONS_DIR, CACHE_DIR, LATEST_CLUSTER_CSV

DELIVERY_RATE_JSON = CACHE_DIR / "delivery_rate.json"
DELIVERY_FALLBACK = 0.407  # push_result 5/5~6/3 실측 평균 (Snowflake 조회 실패 시 폴백)


class SnapshotMissingError(Exception):
    pass


def delivery_rate(n_day: int = 90, max_age_days: int = 7) -> dict:
    """도달률(reached/sent) — push_result 최근 n_day 집계. cache/delivery_rate.json 에 캐시(TTL).

    모델 CTR 은 도달(reached) 대비라, 발송 대상 수에 곱하면 오픈이 1/도달률 배 과대된다.
    이 도달률을 곱해 '발송 대비' 기대오픈으로 환산한다(분배 페이지 현실화).
    """
    if DELIVERY_RATE_JSON.exists():
        try:
            d = json.load(open(DELIVERY_RATE_JSON, encoding="utf-8"))
            if (datetime.now() - datetime.fromisoformat(d["computed_at"])).days < max_age_days:
                return d
        except Exception:
            pass
    try:
        df = run_query(
            f"""SELECT SUM(N_SENT) s, SUM(N_REACHED) r FROM data_anal.push_result
                WHERE PUSH_AT >= DATEADD(day, -{int(n_day)}, CURRENT_DATE)"""
        )
        df.columns = df.columns.str.lower()
        s, r = float(df.iloc[0]["s"] or 0), float(df.iloc[0]["r"] or 0)
        rate = round(r / s, 4) if s else DELIVERY_FALLBACK
        d = {
            "rate": rate, "n_sent": int(s), "n_reached": int(r), "n_day": int(n_day),
            "basis": f"push_result 최근 {int(n_day)}일 (도달 {int(r):,}/발송 {int(s):,})",
            "computed_at": datetime.now().isoformat(timespec="seconds"),
        }
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(DELIVERY_RATE_JSON, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        return d
    except Exception as e:  # Snowflake 불가 → 폴백 (분배 자체는 막지 않음)
        return {
            "rate": DELIVERY_FALLBACK, "basis": f"fallback {DELIVERY_FALLBACK} — push_result 조회 실패",
            "computed_at": datetime.now().isoformat(timespec="seconds"), "error": str(e)[:200],
        }


def create_allocation(pushes: list[dict], top_k: int, volumes: list[float] | None, seed: int = 42, holdout_pct: float = 0.0) -> dict:
    meta = read_meta()
    if meta is None or not LATEST_CLUSTER_CSV.exists():
        raise SnapshotMissingError("유저 세그먼트 스냅샷이 없어요 — 페이지 ②에서 '유저 세그먼트 최신화하기'를 먼저 실행하세요.")

    art = artifacts.load()
    counts = {int(k): int(v) for k, v in meta["cluster_counts"].items()}
    sizes = {cid: counts.get(cid, 0) for cid in art["MAY_ORDER"]}  # 누락 클러스터 = 0명

    dr = delivery_rate()  # 발송 대비 환산용 도달률 (캐시/폴백)
    result = allocate(pushes, cluster_sizes=sizes, top_k=top_k, volumes=volumes, delivery_rate=dr["rate"])

    # ── 유저 단위 분배: 클러스터별 무작위 셔플(seed) 후 배분 count 만큼 슬라이스 ──
    users = pd.read_csv(LATEST_CLUSTER_CSV, dtype={"USER_SEQ": str, "USER_ID": str})
    rng = np.random.default_rng(seed)
    run_id = uuid.uuid4().hex[:10]
    outdir = ALLOCATIONS_DIR / run_id
    outdir.mkdir(parents=True, exist_ok=True)

    frames: dict[int, list[pd.DataFrame]] = {j: [] for j in range(len(pushes))}
    for row in result["matrix"]:
        cu = users[users["CLUSTER"] == row["cluster"]]
        order = rng.permutation(len(cu))
        start = 0
        for j, cell in sorted(row["allocation"].items()):
            take = min(cell["count"], len(cu) - start)  # 반올림 초과 가드
            if take > 0:
                frames[j].append(cu.iloc[order[start : start + take]])
            start += take

    # ── 발송/대조군 분리: 각 푸시 배정 유저를 무작위 셔플 후 holdout_pct 만큼 대조군(미발송)으로 ──
    csv_rows, holdout_rows = [], []
    for j, p in enumerate(pushes):
        df = (
            pd.concat(frames[j], ignore_index=True)[["USER_ID", "USER_SEQ", "CLUSTER"]]
            if frames[j]
            else pd.DataFrame(columns=["USER_ID", "USER_SEQ", "CLUSTER"])
        )
        if holdout_pct > 0 and len(df):
            df = df.iloc[rng.permutation(len(df))].reset_index(drop=True)  # 클러스터 블록 섞어 무작위 대조군
            n_ctrl = int(round(len(df) * holdout_pct))
            control, treat = df.iloc[:n_ctrl], df.iloc[n_ctrl:]
            control.to_csv(outdir / f"push_{j + 1}_control.csv", index=False)
        else:
            treat, control = df, df.iloc[0:0]
        treat.to_csv(outdir / f"push_{j + 1}.csv", index=False)
        csv_rows.append(len(treat))
        holdout_rows.append(len(control))

    # ── 홀드아웃 보정: 대조군은 미발송이라 오픈 불가 → 기대오픈·커버리지를 실제 발송(treat)분 기준으로 환산 ──
    # allocate 는 전체 배정 Xr 로 계산하므로 holdout_pct>0 이면 1/(1-holdout) 배 과대. target_sends/오픈율은 '배정/1인당'이라 유지.
    if holdout_pct > 0:
        opens_sent_total = 0
        for j, pp in enumerate(result["per_push"]):
            tgt = pp["target_sends"]
            ratio = (csv_rows[j] / tgt) if tgt else 0.0
            pp["expected_opens"] = int(round(pp["expected_opens"] * ratio))
            if pp["full_expected_opens"]:
                pp["click_coverage_pct"] = round(pp["expected_opens"] / pp["full_expected_opens"] * 100, 1)
            opens_sent_total += pp["expected_opens"]
        tot = result["totals"]
        tot["expected_opens"] = opens_sent_total
        tot["click_coverage_pct"] = (
            round(opens_sent_total / tot["full_expected_opens"] * 100, 1) if tot["full_expected_opens"] else None
        )

    response = {
        "run_id": run_id,
        "snapshot_at": meta["snapshot_at"],
        "pool": result["pool"],
        "matrix": result["matrix"],
        "per_push": [
            {
                **pp,
                "download_url": f"/api/v1/allocations/{run_id}/download/{j + 1}",
                "csv_rows": csv_rows[j],
                "holdout_rows": holdout_rows[j],
                "control_download_url": (
                    f"/api/v1/allocations/{run_id}/download/{j + 1}?arm=control" if holdout_rows[j] > 0 else None
                ),
            }
            for j, pp in enumerate(result["per_push"])
        ],
        "totals": result["totals"],
        "delivery_rate": dr["rate"],
        "delivery_basis": dr.get("basis", ""),
    }
    with open(outdir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(
            {"request": {"pushes": pushes, "top_k": top_k, "volumes": volumes, "seed": seed, "holdout_pct": holdout_pct},
             "response": response},
            f, ensure_ascii=False, indent=2, default=str,
        )
    return response


def csv_path(run_id: str, push_no: int, arm: str = "treatment"):
    if not run_id.isalnum():
        return None
    name = f"push_{push_no}_control.csv" if arm == "control" else f"push_{push_no}.csv"
    p = ALLOCATIONS_DIR / run_id / name
    return p if p.exists() else None
