"""발송 가능 전체 모수 — 마케팅팀 표준 쿼리 채용 (2026-06-04 사용자 제공).

기준: 최근 N일 내 라이브(media_type=3) 시청 이력이 있고,
      유효 회원(user_status<>3, user_type IN (2,5)) 이며,
      수신거부/제외번호가 아닌(marketing_push='Y') 유저.
"""


def audience_sql(n_day: int = 180) -> str:
    n_day = int(n_day)  # SQL 인젝션 방지 겸 형 강제
    return f"""
    SELECT m.user_seq                AS USER_SEQ,
           TO_VARCHAR(m.user_id)     AS USER_ID
    FROM (
        SELECT DISTINCT vml.user_seq
        FROM data_anal.view_media_log vml
        JOIN grip_db.content_product_category cpc ON vml.media_seq = cpc.content_seq
        JOIN grip_db.category_depth cd            ON cpc.category_seq = cd.category_seq
        WHERE vml.media_type = 3
          AND vml.user_type <> 1
          AND vml.view_datetime >= DATEADD(day, -{n_day}, CURRENT_DATE)
    ) t
    JOIN grip_db.member m        ON m.user_seq = t.user_seq
    WHERE m.user_status <> 3
      AND m.user_type IN (2, 5)
      -- 마케팅 쿼리의 COALESCE(IFF(제외번호,'N',marketing_push),'N')='Y' 와 동치를
      -- EXISTS/NOT EXISTS 로 표현 — LEFT JOIN fan-out(중복행) 원천 차단 (유저당 1행 보장)
      AND EXISTS (
          SELECT 1 FROM grip_db.settings s
          WHERE s.user_seq = m.user_seq AND s.marketing_push = 'Y'
      )
      AND NOT EXISTS (
          SELECT 1 FROM grip_db.exclude ecd
          WHERE ecd.data = m.phone_number
      )
    """
