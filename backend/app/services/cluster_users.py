"""클러스터 내 유저 샘플 분석 — 선택 유저 프로필 조회 (page2). 모든 수치 최근 90일 기준.

오픈 유저 '목록'은 segment_conversion.json 의 cluster_user_samples 에 함께 적재되므로 여기선 다루지 않는다.
'조회' 시 단일 user_seq 의 프로필(등급·성별·나이·지출·구매·객단가·시청 상위방송)만 fast 조회한다.
run_query(호출마다 새 연결, %s 바인딩, temp table 없음).
"""

from datetime import datetime

import pandas as pd

from core.datasources.snowflake_kp import run_query

WINDOW_DAYS = 90      # 지출·구매·시청 집계 기간
TOP_BROADCASTS = 5    # 최근 시청 상위 방송 수


def _parse_age(birth) -> int | None:
    """BIRTH(자유형 날짜 문자열, 흔히 NULL) → 연도 기반 나이. 파싱 실패/비현실값이면 None."""
    if birth is None or (isinstance(birth, float) and pd.isna(birth)):
        return None
    try:
        year = pd.Timestamp(str(birth)).year
    except (ValueError, TypeError):
        return None
    age = datetime.now().year - year
    return age if 0 < age < 130 else None


def fetch_user_profile(user_seq: int) -> dict | None:
    """선택 유저의 기본 프로필/행동 요약. 회원이 없으면 None(→ 404)."""
    user_seq = int(user_seq)

    mem = run_query(
        """
        SELECT m.user_seq, TO_VARCHAR(m.user_id) AS user_id, m.user_name,
               COALESCE(g.grade, 10) AS grade, m.gender, m.birth
        FROM   grip_db.member m
        LEFT JOIN grip_db_realtime.member_grade g ON g.user_seq = m.user_seq
        WHERE  m.user_seq = %s
        """,
        (user_seq,),
    )
    mem.columns = mem.columns.str.lower()
    if not len(mem):
        return None
    r = mem.iloc[0]

    spend = run_query(
        f"""
        SELECT COALESCE(SUM(gmv), 0) AS total_spend,
               COUNT(DISTINCT order_seq) AS purchase_count
        FROM   default.order_all
        WHERE  user_seq = %s AND cancel_at IS NULL AND gmv > 0
          AND  ordered_at >= DATEADD(day, -{WINDOW_DAYS}, CURRENT_TIMESTAMP)
        """,
        (user_seq,),
    )
    spend.columns = spend.columns.str.lower()
    total_spend = int(spend.iloc[0]["total_spend"]) if len(spend) else 0
    purchase_count = int(spend.iloc[0]["purchase_count"]) if len(spend) else 0
    aov = round(total_spend / purchase_count) if purchase_count else 0

    watch = run_query(
        f"""
        WITH w AS (
            SELECT es.contentseq AS content_seq, SUM(es.viewtimemillis) AS ms
            FROM   default.elasticsearch es
            WHERE  es.userseq = %s AND es.logtype = 'VIEW_CONTENT' AND es.viewtimemillis > 0
              AND  es."@timestamp" >= DATEADD(day, -{WINDOW_DAYS}, CURRENT_TIMESTAMP)
            GROUP BY es.contentseq
        )
        SELECT ct.title AS title, mb.user_name AS seller, w.ms / 1000.0 AS watch_sec
        FROM   w
        JOIN   grip_db_realtime.content ct ON ct.content_seq = w.content_seq
        LEFT JOIN grip_db_realtime.member mb ON mb.user_seq = ct.user_seq
        ORDER BY w.ms DESC
        LIMIT {TOP_BROADCASTS}
        """,
        (user_seq,),
    )
    watch.columns = watch.columns.str.lower()
    top_broadcasts = [
        {
            "seller": str(x.seller) if pd.notna(x.seller) else "",
            "title": str(x.title) if pd.notna(x.title) else "",
            "watch_sec": int(round(float(x.watch_sec))),  # 총 시청 초 (분·초 표시는 프론트에서)
        }
        for x in watch.itertuples(index=False)
    ]

    grade = pd.to_numeric(r["grade"], errors="coerce")
    return {
        "user_seq": int(r["user_seq"]),
        "user_id": str(r["user_id"]),
        "user_name": str(r["user_name"]) if pd.notna(r["user_name"]) else "",
        "grade": int(grade) if pd.notna(grade) else 10,
        "gender": str(r["gender"]) if pd.notna(r["gender"]) else None,
        "age": _parse_age(r["birth"]),
        "total_spend": total_spend,
        "purchase_count": purchase_count,
        "aov": aov,
        "top_broadcasts": top_broadcasts,
    }
