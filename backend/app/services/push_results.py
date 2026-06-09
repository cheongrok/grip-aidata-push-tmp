"""푸시 현황 — data_anal.push_result 조회 (페이지 ①). 단발 조회라 새 연결(run_query) 사용."""

import pandas as pd

from core.datasources.snowflake_kp import run_query


def fetch_push_results() -> list[dict]:
    df = run_query("""
        SELECT PUSH_SEQ, TITLE, PUSH_AT, N_SENT, N_REACHED, N_OPEN,
               OPEN_RATE_SENT, OPEN_RATE_REACH, CATEGORY, UPDATED_AT
        FROM data_anal.push_result
        ORDER BY PUSH_AT DESC
    """)
    rows = []
    for r in df.itertuples(index=False):
        rows.append(
            {
                "push_seq": int(r.PUSH_SEQ),
                "title": r.TITLE,
                "push_at": str(pd.Timestamp(r.PUSH_AT)) if pd.notna(r.PUSH_AT) else None,
                "n_sent": int(r.N_SENT) if pd.notna(r.N_SENT) else None,
                "n_reached": int(r.N_REACHED) if pd.notna(r.N_REACHED) else None,
                "n_open": int(r.N_OPEN) if pd.notna(r.N_OPEN) else None,
                "open_rate_sent": float(r.OPEN_RATE_SENT) if pd.notna(r.OPEN_RATE_SENT) else None,
                "open_rate_reach": float(r.OPEN_RATE_REACH) if pd.notna(r.OPEN_RATE_REACH) else None,
                "category": r.CATEGORY,
                "updated_at": str(pd.Timestamp(r.UPDATED_AT)) if pd.notna(r.UPDATED_AT) else None,
            }
        )
    return rows
