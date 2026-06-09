"""오픈 후 전환 펀넬 — 지정 content 의 오픈→유효시청→구매 (유저 distinct, 발송 이후).

push_mapping.discover_content 의 전환 미리보기 계산에 쓰인다(push_at 직접 주입). 집계 기준:
- 오픈   = events_all notification_open, label 'push-{push_seq}', timestamp >= push_at, distinct login_user_seq
- 유효시청 = 오픈 유저가 push_at 이후 지정 content 를 viewtimemillis>=10000 시청
- 구매   = 오픈 유저가 push_at 이후 지정 content 구매 (order_all, cancel_at IS NULL, gmv>0, ordered_at >= push_at)

content_seqs·push_seq 는 정수, push_at 은 호출자가 만든 DB 시각값 — f-string 주입 위험 없음.
Snowflake 는 run_query(호출마다 새 연결, 스레드 안전) — temp table 없이 단일 CTE.
"""

import pandas as pd

from core.datasources.snowflake_kp import run_query


def _funnel_sql(push_seq: int, content_seqs: list[int], push_at: str) -> str:
    csv = ", ".join(str(int(c)) for c in content_seqs)
    return f"""
    WITH opens AS (        -- 푸시 오픈 유저 (push_at 이후, distinct)
        SELECT DISTINCT e.login_user_seq AS user_seq
        FROM   default.events_all e
        WHERE  e.event_name = 'notification_open'
          AND  e.notification_label = 'push-{int(push_seq)}'
          AND  e.timestamp >= TIMESTAMP '{push_at}'
          AND  e.login_user_seq IS NOT NULL
    ),
    watch AS (             -- 오픈 유저 × 지정 content 유효시청(>=10s), push_at 이후
        SELECT DISTINCT es.contentseq AS content_seq, es.userseq AS user_seq
        FROM   default.elasticsearch es
        JOIN   opens o ON o.user_seq = es.userseq
        WHERE  es.contentseq IN ({csv})
          AND  es.viewtimemillis >= 10000
          AND  es."@timestamp" >= TIMESTAMP '{push_at}'
    ),
    buy AS (               -- 오픈 유저 × 지정 content 구매(취소제외, gmv>0), ordered_at push_at 이후
        SELECT DISTINCT ord.content_seq AS content_seq, ord.user_seq AS user_seq
        FROM   default.order_all ord
        JOIN   opens o ON o.user_seq = ord.user_seq
        WHERE  ord.content_seq IN ({csv})
          AND  ord.cancel_at IS NULL
          AND  ord.gmv > 0
          AND  ord.ordered_at >= TIMESTAMP '{push_at}'
    )
    SELECT 'open'  AS kind, CAST(NULL AS NUMBER) AS content_seq, user_seq FROM opens
    UNION ALL
    SELECT 'watch' AS kind, content_seq, user_seq FROM watch
    UNION ALL
    SELECT 'buy'   AS kind, content_seq, user_seq FROM buy
    """


def compute_funnel(push_seq: int, content_seqs: list[int], push_at: str) -> dict:
    """push_seq + 지정 content_seqs 의 오픈→유효시청→구매 펀넬 (유저 distinct, 발송 이후 기준)."""
    content_seqs = sorted({int(c) for c in content_seqs})
    if not content_seqs:
        raise ValueError("content_seqs 가 비어 있습니다")

    df = run_query(_funnel_sql(push_seq, content_seqs, push_at))
    df.columns = df.columns.str.lower()

    if len(df):
        df["user_seq"] = df["user_seq"].astype("int64")
        watch = df[df["kind"] == "watch"].copy()
        buy = df[df["kind"] == "buy"].copy()
        n_open = int(df[df["kind"] == "open"]["user_seq"].nunique())
        n_valid_watch = int(watch["user_seq"].nunique())
        n_purchase = int(buy["user_seq"].nunique())
        wc = watch.assign(content_seq=watch["content_seq"].astype("int64")).groupby("content_seq")["user_seq"].nunique()
        bc = buy.assign(content_seq=buy["content_seq"].astype("int64")).groupby("content_seq")["user_seq"].nunique()
    else:
        n_open = n_valid_watch = n_purchase = 0
        wc = bc = pd.Series(dtype="int64")

    by_content = [
        {"content_seq": int(c), "n_valid_watch": int(wc.get(c, 0)), "n_purchase": int(bc.get(c, 0))}
        for c in content_seqs
    ]
    return {
        "push_seq": int(push_seq),
        "n_open": n_open,
        "n_valid_watch": n_valid_watch,
        "n_purchase": n_purchase,
        "by_content": by_content,
    }
