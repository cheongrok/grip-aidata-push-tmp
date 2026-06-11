"""현재시점 유저 피처 적재 + 클러스터 스냅샷 갱신.

클러스터적용 노트북 load_features(push_seq) 의 변형: 코호트 = 발송가능 전체 모수(audience),
기준시점 = 지금(at=CURRENT_TIMESTAMP), IS_OPEN 없음(스코어링 전용). temp-table 파이프라인 구조 동일.

주의: run_ddl→run_snowflake 가 같은 세션이어야 하므로(TEMPORARY TABLE)
      반드시 단일 스레드(job Lock)에서 호출할 것. 회당 Snowflake 약 3~15분 소요.
"""

import json
import time
from datetime import datetime
from typing import Callable

import pandas as pd

from core.datasources.snowflake_kp import run_ddl, run_snowflake
from core.push import artifacts
from core.push.audience import audience_sql
from core.push.clustering import predict_clusters
from core.push.paths import CACHE_DIR, LATEST_CLUSTER_CSV, LATEST_CLUSTER_META

# 시간대 버킷 SQL 조각 ({t} = 타임스탬프 컬럼) — 노트북과 동일
_HB = """
        CASE WHEN HOUR({t}) BETWEEN 0  AND 5  THEN '0_dawn(0-5)'
             WHEN HOUR({t}) BETWEEN 6  AND 11 THEN '1_morning(6-11)'
             WHEN HOUR({t}) BETWEEN 12 AND 17 THEN '2_afternoon(12-17)'
             ELSE                                  '3_evening(18-23)'
        END"""


def load_features_now(n_day: int = 180, progress: Callable[[str], None] = lambda s: None) -> pd.DataFrame:
    """발송가능 모수 전체의 '지금 시점' 피처 DataFrame (USER_SEQ, USER_ID + 클러스터링 피처 7종)."""
    at = str(pd.Timestamp(run_snowflake("SELECT CURRENT_TIMESTAMP()::timestamp_ntz AS T").iloc[0]["T"]))

    progress("모수 조회 (마케팅 기준)")
    run_ddl(f"CREATE OR REPLACE TEMPORARY TABLE _coh AS {audience_sql(n_day)}")
    n_aud = int(run_snowflake("SELECT COUNT(*) AS N FROM _coh").iloc[0]["N"])
    progress(f"모수 {n_aud:,}명 — 행동 피처 적재 (3개월)")

    # ── events_all 3개월 1회 스캔 → click(상품) + hour ──
    run_ddl(f"""
        CREATE OR REPLACE TEMPORARY TABLE _ev3m AS
        SELECT login_user_seq                  AS user_seq,
               page_product_id,
               event_name,
               {_HB.format(t='timestamp')}     AS hour_bucket
        FROM   default.events_all
        WHERE  event_name IN ('click', 'page_view')
          AND  timestamp <  TIMESTAMP '{at}'
          AND  timestamp >= DATEADD(month, -3, TIMESTAMP '{at}')
          AND  login_user_seq IN (SELECT user_seq FROM _coh)
    """)

    # ── elasticsearch 3개월 1회 스캔 → watch + hour ──
    run_ddl(f"""
        CREATE OR REPLACE TEMPORARY TABLE _es3m AS
        SELECT userseq                          AS user_seq,
               contentseq,
               logtype,
               viewtimemillis,
               {_HB.format(t='"@timestamp"')}   AS hour_bucket
        FROM   default.elasticsearch
        WHERE  "@timestamp" <  TIMESTAMP '{at}'
          AND  "@timestamp" >= DATEADD(month, -3, TIMESTAMP '{at}')
          AND  userseq IN (SELECT user_seq FROM _coh)
    """)

    # ── 구매 (3개월, 취소 보정) ──
    run_ddl(f"""
        CREATE OR REPLACE TEMPORARY TABLE _ord3m AS
        SELECT user_seq, product_id, order_seq
        FROM   default.order_all
        WHERE  ordered_at <  TIMESTAMP '{at}'
          AND  ordered_at >= DATEADD(month, -3, TIMESTAMP '{at}')
          AND  (cancel_at IS NULL OR cancel_at >= TIMESTAMP '{at}')
          AND  user_seq IN (SELECT user_seq FROM _coh)
    """)

    # ── product → category 매핑 (flash 우선 fallback) ──
    run_ddl("""
        CREATE OR REPLACE TEMPORARY TABLE _pids AS
        SELECT DISTINCT product_id FROM (
            SELECT page_product_id AS product_id FROM _ev3m
              WHERE event_name = 'click' AND page_product_id IS NOT NULL
            UNION
            SELECT product_id FROM _ord3m WHERE product_id IS NOT NULL
        )
    """)
    run_ddl("""
        CREATE OR REPLACE TEMPORARY TABLE _prodcat AS
        WITH flash AS (
            SELECT product_id, MAX(lv2_category_name) AS cat
            FROM   aibigdata_db.flash_product_info
            WHERE  lv2_category_name IS NOT NULL
              AND  product_id IN (SELECT product_id FROM _pids)
            GROUP BY product_id
        ),
        nonflash AS (
            SELECT pi.product_id, MAX(c.category_name) AS cat
            FROM   grip_db_realtime.product_info pi
            JOIN   grip_db.product_category pc ON pi.product_seq = pc.product_seq
            JOIN   grip_db_realtime.category  c ON pc.category_seq = c.category_seq
            WHERE  c.level = 2
              AND  pi.product_id IN (SELECT product_id FROM _pids)
              AND  pi.product_id NOT IN (SELECT product_id FROM flash)
            GROUP BY pi.product_id
        )
        SELECT product_id, cat FROM flash
        UNION ALL
        SELECT product_id, cat FROM nonflash
    """)

    progress("RECENCY/등급/구매수 집계 (1년)")
    base = run_snowflake(f"""
        WITH rec AS (
            SELECT login_user_seq AS user_seq, MAX(DATE(timestamp)) AS last_visit
            FROM   default.events_all
            WHERE  event_name IN ('click', 'page_view')
              AND  timestamp <  TIMESTAMP '{at}'
              AND  timestamp >= DATEADD(year, -1, TIMESTAMP '{at}')
              AND  login_user_seq IN (SELECT user_seq FROM _coh)
            GROUP BY 1
        ),
        ordc AS (
            SELECT user_seq, COUNT(DISTINCT order_seq) AS oc
            FROM   _ord3m GROUP BY 1
        )
        SELECT c.user_seq                                       AS USER_SEQ,
               c.user_id                                        AS USER_ID,
               DATEDIFF(day, r.last_visit, DATE '{at[:10]}')    AS RECENCY_DAYS,
               COALESCE(g.grade::VARCHAR, '10')                 AS GRADE,
               COALESCE(o.oc, 0)                                AS ORDER_COUNT_3M
        FROM   _coh c
        LEFT JOIN rec  r ON c.user_seq = r.user_seq
        LEFT JOIN grip_db_realtime.member_grade g ON c.user_seq = g.user_seq
        LEFT JOIN ordc o ON c.user_seq = o.user_seq
    """)

    progress("도미넌트 카테고리/시간대 집계")
    watch = run_snowflake("""
        WITH viewed AS (
            SELECT user_seq, contentseq AS content_seq, COUNT(*) AS cnt
            FROM   _es3m
            WHERE  logtype = 'VIEW_CONTENT' AND viewtimemillis >= 10000
            GROUP BY 1, 2
        ),
        cc AS (
            SELECT c.content_seq, MAX(h.category_name) AS cat
            FROM   grip_db_realtime.content c
            JOIN   aibigdata_db.content_thema_hashtag h ON c.content_id = h.content_id
            WHERE  h.category_name IS NOT NULL
              AND  c.content_seq IN (SELECT content_seq FROM viewed)
            GROUP BY c.content_seq
        )
        SELECT v.user_seq AS USER_SEQ, cc.cat AS WATCH_CAT
        FROM   viewed v JOIN cc USING (content_seq)
        GROUP BY v.user_seq, cc.cat
        QUALIFY ROW_NUMBER() OVER (PARTITION BY v.user_seq ORDER BY SUM(v.cnt) DESC, cc.cat) = 1
    """)
    click = run_snowflake("""
        WITH clicks AS (
            SELECT user_seq, page_product_id AS product_id, COUNT(*) AS cnt
            FROM   _ev3m
            WHERE  event_name = 'click' AND page_product_id IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT cl.user_seq AS USER_SEQ, pc.cat AS CLICK_CAT
        FROM   clicks cl JOIN _prodcat pc ON cl.product_id = pc.product_id
        GROUP BY cl.user_seq, pc.cat
        QUALIFY ROW_NUMBER() OVER (PARTITION BY cl.user_seq ORDER BY SUM(cl.cnt) DESC, pc.cat) = 1
    """)
    order = run_snowflake("""
        WITH orders AS (
            SELECT user_seq, product_id, COUNT(DISTINCT order_seq) AS cnt
            FROM   _ord3m
            WHERE  product_id IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT o.user_seq AS USER_SEQ, pc.cat AS ORDER_CAT
        FROM   orders o JOIN _prodcat pc ON o.product_id = pc.product_id
        GROUP BY o.user_seq, pc.cat
        QUALIFY ROW_NUMBER() OVER (PARTITION BY o.user_seq ORDER BY SUM(o.cnt) DESC, pc.cat) = 1
    """)
    hour = run_snowflake("""
        WITH ah AS (
            SELECT user_seq, hour_bucket FROM _ev3m
            UNION ALL
            SELECT user_seq, hour_bucket FROM _es3m
        )
        SELECT user_seq AS USER_SEQ, hour_bucket AS PRIMARY_HOUR_BUCKET
        FROM   ah
        GROUP BY user_seq, hour_bucket
        QUALIFY ROW_NUMBER() OVER (PARTITION BY user_seq ORDER BY COUNT(*) DESC, hour_bucket) = 1
    """)

    # ── merge (노트북과 동일: USER_SEQ 문자열 키) ──
    df = base.copy()
    df["USER_SEQ"] = df["USER_SEQ"].astype(str)
    for extra in (watch, click, order, hour):
        extra["USER_SEQ"] = extra["USER_SEQ"].astype(str)
        df = df.merge(extra, on="USER_SEQ", how="left")
    df["ORDER_COUNT_3M"] = df["ORDER_COUNT_3M"].astype(int)
    df["RECENCY_DAYS"] = pd.to_numeric(df["RECENCY_DAYS"], errors="coerce")
    assert df["USER_SEQ"].is_unique, "USER_SEQ 중복 — 모수 쿼리 또는 머지 키 확인 필요"
    df.attrs["snapshot_at"] = at
    return df


def refresh_snapshot(n_day: int = 180, progress: Callable[[str], None] = lambda s: None) -> dict:
    """모수 적재 → 클러스터 배정 → latest_cluster.csv / latest_cluster_meta.json 저장. 메타 dict 반환."""
    t0 = time.time()
    df = load_features_now(n_day, progress)

    progress(f"{len(df):,}명 세그먼트 배정 (k-prototypes)")
    df["CLUSTER"] = predict_clusters(df)

    progress("저장")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = df[["USER_SEQ", "USER_ID", "CLUSTER"]].copy()
    out["SNAPSHOT_AT"] = df.attrs["snapshot_at"]
    tmp = LATEST_CLUSTER_CSV.with_suffix(".csv.tmp")  # 원자적 교체 — 분배가 부분 파일을 읽지 않도록
    out.to_csv(tmp, index=False)
    tmp.replace(LATEST_CLUSTER_CSV)

    counts = df["CLUSTER"].value_counts().to_dict()
    meta = {
        "snapshot_at": df.attrs["snapshot_at"],
        "refreshed_at": datetime.now().isoformat(timespec="seconds"),
        "n_day": int(n_day),
        "n_users": int(len(df)),
        "cluster_counts": {str(int(k)): int(v) for k, v in counts.items()},
        "duration_sec": round(time.time() - t0, 1),
        "model_fit": artifacts.load()["FIT_META"],
    }
    with open(LATEST_CLUSTER_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta
