"""클러스터 스냅샷 조회/갱신 (페이지 ②)."""

import json
from datetime import datetime, timedelta

from core.push import artifacts
from core.push.descriptions import ACTION, FRIENDLY, SHORT_NAME, TIER, TIER_LABEL
from core.push.paths import LATEST_CLUSTER_META

STALE_DAYS = 7


def read_meta() -> dict | None:
    if not LATEST_CLUSTER_META.exists():
        return None
    with open(LATEST_CLUSTER_META, encoding="utf-8") as f:
        return json.load(f)


def is_stale(meta: dict) -> bool:
    try:
        return datetime.fromisoformat(meta["refreshed_at"]) < datetime.now() - timedelta(days=STALE_DAYS)
    except (KeyError, ValueError):
        return True


def snapshot_response() -> dict | None:
    meta = read_meta()
    if meta is None:
        return None
    art = artifacts.load()
    base, may_order = art["BASE"], art["MAY_ORDER"]
    overall_ctr = base["opens"].sum() / base["n"].sum()
    counts = {int(k): int(v) for k, v in meta["cluster_counts"].items()}
    total = sum(counts.values()) or 1

    rank_of = art["RANK"]
    clusters = []
    for cid in may_order:  # 발송 우선순위 순 표시 (= rank 1~10 순)
        ctr = float(base.loc[cid, "opens"] / base.loc[cid, "n"])
        clusters.append(
            {
                "cluster": cid,
                "rank": rank_of[cid],
                "short_name": SHORT_NAME.get(cid, ""),
                "tier": TIER[cid],
                "tier_label": TIER_LABEL[TIER[cid]],
                "short_desc": art["DESC"].get(cid, ""),
                "friendly_desc": FRIENDLY.get(cid, ""),
                "action": ACTION.get(cid, ""),
                "count": counts.get(cid, 0),
                "share_pct": round(counts.get(cid, 0) / total * 100, 1),
                "may_ctr_pct": round(ctr * 100, 2),
                "may_lift": round(ctr / overall_ctr, 2),
            }
        )
    return {
        "snapshot_at": meta["snapshot_at"],
        "refreshed_at": meta["refreshed_at"],
        "n_day": meta["n_day"],
        "n_users": meta["n_users"],
        "duration_sec": meta["duration_sec"],
        "stale": is_stale(meta),
        "clusters": clusters,
    }
