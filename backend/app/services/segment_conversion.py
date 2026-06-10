"""세그먼트(클러스터)별 전환·커버리지 집계 — 수기 확정 push→방송(content) 매핑 기반.

자동 2시간 top-1 역산(신뢰도 30%대)을 버리고, 운영자가 직접 확정한 매핑
(cache/push_content_id_map.json: {push_seq: [content_id,...]})으로 집계한다.

각 푸시 × 세그먼트(클러스터)에 대해:
- 발송   = data_anal.push_send_history (send_status=Y, token_valid=Y) distinct user_seq → 클러스터
- 도달   = default.events_all 푸시 라벨 수신 distinct login_user_seq → 클러스터
- 오픈   = events_all notification_open(라벨 'push-{seq}', timestamp >= push_at) distinct → 클러스터
- 유효시청 = 오픈 유저가 매핑 content 를 viewtimemillis>=10000 으로 본 시각(@timestamp)이 push_at 이후
- 구매     = 오픈 유저가 매핑 content 를 산(cancel_at IS NULL AND gmv>0) 시각(ordered_at)이 push_at 이후
- 유효시청률/거래전환율 = 유효시청·구매 / 오픈 (오픈자 기준)
- 판매자   = 매핑 content 소유자(grip_db_realtime.content.user_seq) → member.user_name

content_id(영숫자)는 grip_db_realtime.content 로 content_seq(숫자)로 변환해 ES/주문과 조인한다.
오픈/발송/도달 유저를 latest_cluster.csv 로 클러스터에 매핑하고, 스냅샷에 없으면 cluster=-1(미배정).

Snowflake 는 호출마다 새 연결(run_query). job 직렬화는 jobs.py 의 SNOWFLAKE_LOCK 이 보장.
"""

import json
import os
from datetime import datetime
from typing import Callable

import pandas as pd

from core.datasources.snowflake_kp import run_query
from core.push import artifacts
from core.push.descriptions import SHORT_NAME
from core.push.paths import CACHE_DIR, LATEST_CLUSTER_CSV

SEGMENT_CONVERSION_JSON = CACHE_DIR / "segment_conversion.json"
PUSH_CONTENT_ID_MAP_JSON = CACHE_DIR / "push_content_id_map.json"

# 미배정(현재 스냅샷에 없는 유저) 가상 클러스터
UNASSIGNED = -1
UNASSIGNED_DESC = "미배정(스냅샷 외)"


class SnapshotMissingError(Exception):
    """클러스터 스냅샷(latest_cluster.csv)이 없을 때."""


class ManualMapMissingError(Exception):
    """수기 매핑(push_content_id_map.json)이 없거나 content_id 변환 실패."""


def _rate(n: int, n_open: int) -> float:
    """전환율(%) = n / n_open * 100, n_open=0 이면 0.0 (소수 둘째자리 반올림)."""
    return round(float(n) / n_open * 100, 2) if n_open else 0.0


def _stats(
    n_open: int, n_view: int, n_purchase: int,
    n_sent: int = 0, n_reached: int = 0, gmv_sum: float = 0,
) -> dict:
    return {
        "n_sent": int(n_sent),
        "n_reached": int(n_reached),
        "n_open": int(n_open),
        "n_view": int(n_view),
        "n_purchase": int(n_purchase),
        "view_rate_pct": _rate(n_view, n_open),
        "purchase_rate_pct": _rate(n_purchase, n_open),
        "gmv_sum": int(round(gmv_sum)),  # 전환 구매 GMV 합 (원)
        "aov": int(round(gmv_sum / n_purchase)) if n_purchase else 0,  # 객단가 = GMV합 / 구매자수 (1인당, 원)
    }


# ─────────────────────────── 수기 매핑 / content_id·판매자 변환 ───────────────────────────
def _load_manual_map() -> dict[int, dict]:
    """push_content_id_map.json → {push_seq:int → {"content_ids":[str], "category":str|None}}.

    두 포맷 호환: 신규 dict({"content_ids":[...], "category":...}) / 레거시 list([content_id,...] → category=None).
    """
    if not PUSH_CONTENT_ID_MAP_JSON.exists():
        raise ManualMapMissingError(
            f"수기 매핑 파일 없음: {PUSH_CONTENT_ID_MAP_JSON} — push→content_id 매핑을 먼저 채우세요."
        )
    with open(PUSH_CONTENT_ID_MAP_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[int, dict] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            cids = [str(c) for c in v.get("content_ids", [])]
            cat = v.get("category")
        else:  # 레거시 list 포맷
            cids = [str(c) for c in v]
            cat = None
        if cids:
            out[int(k)] = {"content_ids": cids, "category": cat}
    return out


def _resolve_content_seqs(content_ids: list[str]) -> dict[str, int]:
    """content_id(영숫자) → content_seq(숫자). 영숫자만 허용해 주입 차단."""
    ids = sorted({c for c in content_ids if c.isalnum()})
    if not ids:
        return {}
    inlist = ", ".join("'" + c + "'" for c in ids)
    df = run_query(
        f"SELECT content_id, content_seq FROM grip_db_realtime.content WHERE content_id IN ({inlist})"
    )
    df.columns = df.columns.str.lower()
    return {str(r.content_id): int(r.content_seq) for r in df.itertuples(index=False)}


def _resolve_sellers(content_seqs: list[int]) -> dict[int, str]:
    """content_seq → 판매자명(소유자 member.user_name). grip_db_realtime.content × member."""
    seqs = sorted({int(c) for c in content_seqs})
    if not seqs:
        return {}
    inlist = ", ".join(str(c) for c in seqs)
    df = run_query(f"""
        SELECT ct.content_seq, mb.user_name AS seller
        FROM   grip_db_realtime.content ct
        LEFT JOIN grip_db_realtime.member mb ON mb.user_seq = ct.user_seq
        WHERE  ct.content_seq IN ({inlist})
    """)
    df.columns = df.columns.str.lower()
    return {int(r.content_seq): (str(r.seller) if pd.notna(r.seller) else "") for r in df.itertuples(index=False)}


# ─────────────────────────── 집계 SQL ───────────────────────────
def _meta_sql(push_seqs: list[int]) -> str:
    """매핑된 푸시의 발송시각(send_history)·제목(grip_db.push)·카테고리(push_result)."""
    inlist = ", ".join(str(int(ps)) for ps in push_seqs)
    return f"""
    WITH pa AS (
        SELECT push_seq, MIN(push_at) AS push_at
        FROM   data_anal.push_send_history
        WHERE  send_status = 'Y' AND push_seq IN ({inlist})
        GROUP BY push_seq
    ),
    titles AS (
        SELECT push_seq, ANY_VALUE(title) AS title
        FROM   grip_db.push WHERE push_seq IN ({inlist}) GROUP BY push_seq
    )
    SELECT pa.push_seq, pa.push_at, t.title, pr.category
    FROM   pa
    LEFT JOIN titles t ON t.push_seq = pa.push_seq
    LEFT JOIN data_anal.push_result pr ON pr.push_seq = pa.push_seq
    ORDER BY pa.push_at
    """


def _rowlevel_sql(pmap: dict[int, list[int]], push_at: dict[int, str]) -> str:
    """오픈 유저 단위 (push_seq, user_seq, viewed, purchased) — 수기 매핑 + 시간순서(발송 이후)."""
    seqs = [ps for ps in pmap if ps in push_at]
    pushes_vals = ", ".join(f"({ps}, TIMESTAMP '{push_at[ps]}')" for ps in seqs)
    pmap_vals = ", ".join(f"({ps}, {cs})" for ps in seqs for cs in pmap[ps])
    cs_list = ", ".join(str(cs) for ps in seqs for cs in pmap[ps])
    min_pa = min(push_at[ps] for ps in seqs)
    return f"""
    WITH pushes AS (SELECT * FROM (VALUES {pushes_vals}) AS p(push_seq, push_at)),
    pmap AS (SELECT * FROM (VALUES {pmap_vals}) AS m(push_seq, content_seq)),
    opens AS (
        SELECT p.push_seq, e.login_user_seq AS user_seq
        FROM   default.events_all e
        JOIN   pushes p ON e.notification_label = 'push-' || p.push_seq AND e.timestamp >= p.push_at
        WHERE  e.event_name = 'notification_open' AND e.login_user_seq IS NOT NULL
          AND  e.timestamp >= TIMESTAMP '{min_pa}'
        GROUP BY p.push_seq, e.login_user_seq
    ),
    es_watch AS (
        SELECT es.userseq AS user_seq, es.contentseq AS content_seq, es."@timestamp" AS ts
        FROM   default.elasticsearch es
        WHERE  es.contentseq IN ({cs_list}) AND es.viewtimemillis >= 10000
          AND  es."@timestamp" >= TIMESTAMP '{min_pa}'
    ),
    watch AS (
        SELECT DISTINCT o.push_seq, o.user_seq
        FROM   opens o
        JOIN   pmap pm    ON pm.push_seq = o.push_seq
        JOIN   pushes p   ON p.push_seq = o.push_seq
        JOIN   es_watch w ON w.user_seq = o.user_seq AND w.content_seq = pm.content_seq AND w.ts >= p.push_at
    ),
    ord_buy AS (
        SELECT ord.user_seq, ord.content_seq, ord.ordered_at AS ts, ord.gmv AS gmv
        FROM   default.order_all ord
        WHERE  ord.content_seq IN ({cs_list}) AND ord.cancel_at IS NULL AND ord.gmv > 0
          AND  ord.ordered_at >= TIMESTAMP '{min_pa}'
    ),
    buy AS (   -- (push_seq, user_seq) 구매자별 1행 + 매핑 방송 전환구매 GMV 합 (구매자당 합산 → 객단가 분자)
        SELECT o.push_seq, o.user_seq, SUM(b.gmv) AS gmv
        FROM   opens o
        JOIN   pmap pm   ON pm.push_seq = o.push_seq
        JOIN   pushes p  ON p.push_seq = o.push_seq
        JOIN   ord_buy b ON b.user_seq = o.user_seq AND b.content_seq = pm.content_seq AND b.ts >= p.push_at
        GROUP BY o.push_seq, o.user_seq
    )
    SELECT o.push_seq, o.user_seq,
           IFF(w.user_seq IS NOT NULL, 1, 0) AS viewed,
           IFF(b.user_seq IS NOT NULL, 1, 0) AS purchased,
           COALESCE(b.gmv, 0) AS gmv
    FROM   opens o
    LEFT JOIN watch w ON w.push_seq = o.push_seq AND w.user_seq = o.user_seq
    LEFT JOIN buy   b ON b.push_seq = o.push_seq AND b.user_seq = o.user_seq
    """


def _send_sql(push_seqs: list[int]) -> str:
    """푸시별 발송 유저 (push_seq, user_seq) distinct — 발송 모수(send_status=Y·token_valid=Y)."""
    inlist = ", ".join(str(int(p)) for p in push_seqs)
    return f"""
    SELECT push_seq, user_seq
    FROM   data_anal.push_send_history
    WHERE  send_status = 'Y' AND token_valid = 'Y' AND push_seq IN ({inlist})
    GROUP BY push_seq, user_seq
    """


def _reach_sql(push_seqs: list[int], min_pa: str) -> str:
    """푸시별 도달 유저 (push_seq, user_seq) distinct — events_all 푸시 라벨 수신."""
    labels = ", ".join("'push-" + str(int(p)) + "'" for p in push_seqs)
    return f"""
    SELECT TRY_TO_NUMBER(SPLIT_PART(notification_label, '-', 2)) AS push_seq,
           login_user_seq AS user_seq
    FROM   default.events_all
    WHERE  notification_label IN ({labels})
      AND  login_user_seq IS NOT NULL
      AND  timestamp >= DATEADD(day, -1, TIMESTAMP '{min_pa}')
    GROUP BY 1, 2
    """


def _load_clusters() -> pd.Series:
    """latest_cluster.csv → USER_SEQ(int) → CLUSTER(int) Series (대용량 매핑 효율 위해 int 인덱스)."""
    if not LATEST_CLUSTER_CSV.exists():
        raise SnapshotMissingError("클러스터 스냅샷 없음 — 유저 클러스터 최신화 먼저")
    cl = pd.read_csv(LATEST_CLUSTER_CSV, usecols=["USER_SEQ", "CLUSTER"])
    return pd.Series(cl["CLUSTER"].astype(int).values, index=cl["USER_SEQ"].astype("int64"))


def _snapshot_at() -> str:
    """latest_cluster.csv 의 SNAPSHOT_AT 첫 값 (초 단위로 절삭)."""
    head = pd.read_csv(LATEST_CLUSTER_CSV, usecols=["SNAPSHOT_AT"], nrows=1)
    ts = pd.Timestamp(head["SNAPSHOT_AT"].iloc[0])
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _cluster_block(g: pd.DataFrame, desc: dict, k: int, sent_s: pd.Series, reach_s: pd.Series) -> list[dict]:
    """클러스터별 stats 리스트 (0..k-1 + 미배정(-1), 항상 전부 포함, 누락은 0).

    g: 오픈 유저 그룹(cluster·viewed·purchased), sent_s/reach_s: 그 푸시의 클러스터별 발송/도달 카운트.
    오픈 0건이어도 발송/도달이 있을 수 있으므로 다섯 지표를 각자의 소스에서 채운다.
    rank·short_name 부여와 정렬은 read 시점 _rank_and_sort 가 한다.
    """
    if len(g):
        agg = g.groupby("cluster").agg(
            n_open=("user_seq", "size"), n_view=("viewed", "sum"),
            n_purchase=("purchased", "sum"), gmv_sum=("gmv", "sum"),
        )
    else:
        agg = pd.DataFrame(columns=["n_open", "n_view", "n_purchase", "gmv_sum"])
    rows = []
    for cid in list(range(k)) + [UNASSIGNED]:
        if cid in agg.index:
            r = agg.loc[cid]
            n_open, n_view, n_purchase = int(r["n_open"]), int(r["n_view"]), int(r["n_purchase"])
            gmv_sum = float(r["gmv_sum"])
        else:
            n_open = n_view = n_purchase = 0
            gmv_sum = 0.0
        rows.append(
            {
                "cluster": int(cid),
                "desc": UNASSIGNED_DESC if cid == UNASSIGNED else desc.get(int(cid), ""),
                **_stats(n_open, n_view, n_purchase, int(sent_s.get(cid, 0)), int(reach_s.get(cid, 0)), gmv_sum),
            }
        )
    return rows


def _atomic_write(path, payload: dict) -> None:
    """tmp 파일 작성 후 os.replace 로 원자적 교체 (부분 파일 읽힘 방지)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _by_cluster_for_push(grp: pd.Series, pseq: int) -> pd.Series:
    """MultiIndex(push_seq, cluster) 집계에서 한 푸시의 cluster→count Series 추출 (없으면 빈 Series)."""
    if len(grp) and pseq in grp.index.get_level_values(0):
        return grp.loc[pseq]
    return pd.Series(dtype="int64")


def compute_segment_conversion(progress: Callable[[str], None] = lambda s: None) -> dict:
    """세그먼트별 전환·커버리지 집계 job 본체 (수기 매핑 기반). jobs.start_exclusive 로 실행."""
    desc = artifacts.load()["DESC"]
    k = artifacts.load()["K"]

    progress("수기 매핑 로드")
    manual = _load_manual_map()  # {push_seq: {"content_ids": [...], "category": str|None}}
    if not manual:
        raise ManualMapMissingError("수기 매핑이 비어 있습니다.")
    push_seqs = sorted(manual)

    progress("클러스터 스냅샷 로드")
    cmap = _load_clusters()  # USER_SEQ(int) → CLUSTER. 없으면 SnapshotMissingError
    snapshot_at = _snapshot_at()

    progress("content_id → content_seq 변환")
    all_ids = sorted({c for v in manual.values() for c in v["content_ids"]})
    id2seq = _resolve_content_seqs(all_ids)
    missing = [c for c in all_ids if c not in id2seq]
    if missing:
        raise ManualMapMissingError(f"content_id 변환 실패({len(missing)}건): {missing[:10]}")
    pmap = {ps: [id2seq[c] for c in manual[ps]["content_ids"]] for ps in push_seqs}  # push → [content_seq]

    progress("판매자/푸시 메타 수집")
    sellers = _resolve_sellers([cs for css in pmap.values() for cs in css])  # content_seq → 판매자명
    push_seller = {
        ps: ", ".join(sorted({sellers.get(cs, "") for cs in pmap[ps] if sellers.get(cs)}))
        for ps in push_seqs
    }
    meta = run_query(_meta_sql(push_seqs))
    meta.columns = meta.columns.str.lower()
    push_at = {
        int(m.push_seq): pd.Timestamp(m.push_at).strftime("%Y-%m-%d %H:%M:%S")
        for m in meta.itertuples(index=False)
        if pd.notna(m.push_at)
    }
    min_pa = min(push_at.values())

    progress("오픈 → 유효시청 → 구매 집계 (Snowflake · 발송 이후만)")
    rl = run_query(_rowlevel_sql(pmap, push_at))
    rl.columns = rl.columns.str.lower()

    progress("세그먼트별 발송/도달 집계 (Snowflake)")
    send_df = run_query(_send_sql(push_seqs))
    send_df.columns = send_df.columns.str.lower()
    reach_df = run_query(_reach_sql(push_seqs, min_pa))
    reach_df.columns = reach_df.columns.str.lower()

    progress("클러스터 매핑/집계")

    def _to_cluster(s: pd.Series) -> pd.Series:
        return s.astype("int64").map(cmap).fillna(UNASSIGNED).astype(int)

    if len(rl):
        rl["cluster"] = _to_cluster(rl["user_seq"])
        rl["viewed"] = rl["viewed"].astype(int)
        rl["purchased"] = rl["purchased"].astype(int)
        rl["gmv"] = pd.to_numeric(rl["gmv"], errors="coerce").fillna(0.0)
    else:
        rl = pd.DataFrame(columns=["push_seq", "user_seq", "viewed", "purchased", "gmv", "cluster"])

    if len(send_df):
        send_df["cluster"] = _to_cluster(send_df["user_seq"])
    if len(reach_df):
        reach_df["cluster"] = _to_cluster(reach_df["user_seq"])

    sent_by = send_df.groupby(["push_seq", "cluster"]).size() if len(send_df) else pd.Series(dtype="int64")
    reach_by = reach_df.groupby(["push_seq", "cluster"]).size() if len(reach_df) else pd.Series(dtype="int64")
    sent_total = send_df.groupby("cluster").size() if len(send_df) else pd.Series(dtype="int64")
    reach_total = reach_df.groupby("cluster").size() if len(reach_df) else pd.Series(dtype="int64")
    open_by_push = {int(pseq): grp for pseq, grp in rl.groupby("push_seq")} if len(rl) else {}
    empty_open = pd.DataFrame(columns=["user_seq", "viewed", "purchased", "gmv", "cluster"])

    # ── 푸시별 결과 (메타 순서 = push_at 오름차순) ──
    pushes_out = []
    for m in meta.itertuples(index=False):
        pseq = int(m.push_seq)
        g = open_by_push.get(pseq, empty_open)
        sent_s = _by_cluster_for_push(sent_by, pseq)
        reach_s = _by_cluster_for_push(reach_by, pseq)
        overall = _stats(
            len(g),
            int(g["viewed"].sum()) if len(g) else 0,
            int(g["purchased"].sum()) if len(g) else 0,
            int(sent_s.sum()),
            int(reach_s.sum()),
            float(g["gmv"].sum()) if len(g) else 0.0,
        )
        # 카테고리: 매핑에 저장된 값 우선(신규 푸시는 push_result 미적재일 수 있음), 없으면 push_result
        cat = manual.get(pseq, {}).get("category") or (m.category if pd.notna(m.category) else None)
        pushes_out.append(
            {
                "push_seq": pseq,
                "title": m.title if pd.notna(m.title) else None,
                "category": cat,
                "push_at": str(pd.Timestamp(m.push_at)) if pd.notna(m.push_at) else None,
                "seller_name": push_seller.get(pseq, ""),
                "content_ids": manual.get(pseq, {}).get("content_ids", []),
                "content_seqs": pmap.get(pseq, []),
                "overall": overall,
                "clusters": _cluster_block(g, desc, k, sent_s, reach_s),
            }
        )

    # ── 전체 totals / by_cluster_total (푸시 합산 — 같은 유저가 여러 푸시면 중복 계수) ──
    totals = _stats(
        len(rl),
        int(rl["viewed"].sum()) if len(rl) else 0,
        int(rl["purchased"].sum()) if len(rl) else 0,
        int(len(send_df)),
        int(len(reach_df)),
        float(rl["gmv"].sum()) if len(rl) else 0.0,
    )
    by_cluster_total = _cluster_block(rl, desc, k, sent_total, reach_total)

    dates = sorted(v[:10] for v in push_at.values())
    period_start, period_end = (dates[0], dates[-1]) if dates else ("", "")

    payload = {
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "cluster_snapshot_at": snapshot_at,
        "period_start": period_start,
        "period_end": period_end,
        "n_pushes": int(len(meta)),
        "mapping": "manual",
        "totals": totals,
        "by_cluster_total": by_cluster_total,
        "pushes": pushes_out,
    }

    progress("저장")
    _atomic_write(SEGMENT_CONVERSION_JSON, payload)
    return payload


def _rank_and_sort(rows: list[dict]) -> list[dict]:
    """클러스터 행에 표시 번호(rank 1~10)·짧은 별칭 부여 + 우선순위 순 정렬 (미배정은 rank=None, 맨 뒤).

    집계 본체는 클러스터 id 오름차순으로 저장하므로, 화면 계약(발송 우선순위 1~10 순)은
    여기 read 시점에서 입힌다 — 기존 캐시(rank 없음)도 재집계 없이 그대로 정렬·표기된다.
    """
    rank_of = artifacts.load()["RANK"]
    for r in rows:
        cid = int(r["cluster"])
        r["rank"] = rank_of.get(cid)  # 미배정(-1) → None
        r["short_name"] = SHORT_NAME.get(cid, "")  # 미배정 → ""
    return sorted(rows, key=lambda r: (r["rank"] is None, r["rank"] or 0))


def read_segment_conversion() -> dict | None:
    """캐시된 집계 결과 (없으면 None). 클러스터 행은 rank·별칭 부여 후 우선순위 순으로 정렬해 반환."""
    if not SEGMENT_CONVERSION_JSON.exists():
        return None
    with open(SEGMENT_CONVERSION_JSON, encoding="utf-8") as f:
        payload = json.load(f)
    payload["by_cluster_total"] = _rank_and_sort(payload["by_cluster_total"])
    for p in payload["pushes"]:
        p["clusters"] = _rank_and_sort(p["clusters"])
    return payload
