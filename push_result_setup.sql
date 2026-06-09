-- =============================================================================
-- 푸시 결과 적재 — Snowflake 테이블 + 수동 upsert 프로시저 + 대시보드 뷰
-- =============================================================================
-- 목적: push_seq 를 입력하면 해당 푸시의 발송/도달/오픈 결과를 계산해
--        PROD_GRIP_DW.DATA_ANAL.PUSH_RESULT 에 upsert. Snowsight 대시보드 소스.
--
-- 운영 방식: 스케줄 없음(수동). 푸시 발송 후 오픈이 며칠에 걸쳐 누적되므로,
--            발송 2~3일 뒤 push_seq 를 직접 넣어 CALL → 안정된 수치 적재.
--            나중에 갱신하고 싶으면 같은 push_seq 로 다시 CALL(멱등, MERGE).
--
-- 데이터 소스 / 지표 정의:
--   n_sent    = PUSH_SEND_HISTORY (send_status='Y' AND token_valid='Y') distinct user_seq
--   n_reached = EVENTS_ALL(notification_label='push-<seq>') distinct login_user_seq
--   n_open    = 위 중 event_name='notification_open' distinct login_user_seq
--   open_rate_sent  = n_open / n_sent     (발송 대비, 마케터 헤드라인)
--   open_rate_reach = n_open / n_reached  (도달 대비)
--   * events_all(116억행)은 timestamp 로 프루닝(발송시각-1d~) → 라벨당 ~1초.
--   * 노트북 정의와의 차이: 여기 n_reached/n_open 은 발송유저와 교집합하지 않음
--     (오픈 이벤트는 사실상 수신자에서만 발생하므로 차이는 미미: 906 vs 904).
-- =============================================================================

-- 1) 결과 테이블 (푸시 1건 = 1행) ---------------------------------------------
CREATE TABLE IF NOT EXISTS PROD_GRIP_DW.DATA_ANAL.PUSH_RESULT (
  push_seq         NUMBER(38,0),
  title            VARCHAR,
  push_at          TIMESTAMP_NTZ,
  n_sent           NUMBER(38,0),
  n_reached        NUMBER(38,0),
  n_open           NUMBER(38,0),
  open_rate_sent   FLOAT,
  open_rate_reach  FLOAT,
  updated_at       TIMESTAMP_NTZ,
  category         VARCHAR,          -- 수동 유지(프로시저 미관여); 없으면 비상품으로 보고 행 제거 가능
                                     -- 기존 테이블 사후 추가: ALTER TABLE ... ADD COLUMN IF NOT EXISTS category VARCHAR;
  CONSTRAINT pk_push_result PRIMARY KEY (push_seq)
);

-- 2) upsert 프로시저 — 콤마구분 다중 push_seq 허용 ('7100' 또는 '7100,7101') ----
CREATE OR REPLACE PROCEDURE PROD_GRIP_DW.DATA_ANAL.SP_PUSH_RESULT_UPSERT(PUSH_SEQ_LIST VARCHAR)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
  rows_affected INTEGER DEFAULT 0;
BEGIN
  MERGE INTO PROD_GRIP_DW.DATA_ANAL.PUSH_RESULT AS t
  USING (
    WITH tgt AS (
      SELECT DISTINCT TRY_TO_NUMBER(TRIM(s.value)) AS push_seq
      FROM TABLE(SPLIT_TO_TABLE(:PUSH_SEQ_LIST, ',')) AS s
      WHERE TRY_TO_NUMBER(TRIM(s.value)) IS NOT NULL
    ),
    snd AS (
      SELECT push_seq,
             COUNT(DISTINCT user_seq) AS n_sent,
             MIN(push_at)             AS push_at
      FROM PROD_GRIP_DW.DATA_ANAL.PUSH_SEND_HISTORY
      WHERE send_status = 'Y' AND token_valid = 'Y'
        AND push_seq IN (SELECT push_seq FROM tgt)
      GROUP BY push_seq
      HAVING COUNT(DISTINCT user_seq) > 0          -- 발송모수 0/NULL 제외
    ),
    ev AS (
      SELECT TRY_TO_NUMBER(SPLIT_PART(notification_label, '-', 2)) AS push_seq,
             COUNT(DISTINCT login_user_seq) AS n_reached,
             COUNT(DISTINCT CASE WHEN event_name = 'notification_open' THEN login_user_seq END) AS n_open
      FROM PROD_GRIP_DW.DEFAULT.EVENTS_ALL
      WHERE notification_label IN (SELECT 'push-' || push_seq FROM tgt)
        AND timestamp >= (SELECT DATEADD('day', -1, MIN(push_at)) FROM snd)  -- 비용 프루닝
      GROUP BY 1
    ),
    meta AS (
      SELECT push_seq, ANY_VALUE(title) AS title
      FROM PROD_GRIP_DW.GRIP_DB.PUSH
      WHERE push_seq IN (SELECT push_seq FROM tgt)
      GROUP BY push_seq
    )
    SELECT s.push_seq, m.title, s.push_at, s.n_sent,
           COALESCE(e.n_reached, 0) AS n_reached,
           COALESCE(e.n_open, 0)    AS n_open,
           ROUND(COALESCE(e.n_open,0) / NULLIF(s.n_sent,0),   6) AS open_rate_sent,
           ROUND(COALESCE(e.n_open,0) / NULLIF(e.n_reached,0),6) AS open_rate_reach
    FROM snd s
    LEFT JOIN ev   e ON s.push_seq = e.push_seq
    LEFT JOIN meta m ON s.push_seq = m.push_seq
  ) AS src
  ON t.push_seq = src.push_seq
  WHEN MATCHED THEN UPDATE SET
    title=src.title, push_at=src.push_at, n_sent=src.n_sent,
    n_reached=src.n_reached, n_open=src.n_open,
    open_rate_sent=src.open_rate_sent, open_rate_reach=src.open_rate_reach,
    updated_at=CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT
    (push_seq,title,push_at,n_sent,n_reached,n_open,open_rate_sent,open_rate_reach,updated_at)
    VALUES (src.push_seq,src.title,src.push_at,src.n_sent,src.n_reached,src.n_open,
            src.open_rate_sent,src.open_rate_reach,CURRENT_TIMESTAMP());
  rows_affected := SQLROWCOUNT;
  RETURN 'push_result upsert OK | input=[' || :PUSH_SEQ_LIST || '] | rows=' || rows_affected;
END;
$$
;

-- 3) 대시보드 편의 뷰 (오픈율 %, 최신순) --------------------------------------
CREATE OR REPLACE VIEW PROD_GRIP_DW.DATA_ANAL.V_PUSH_RESULT AS
SELECT push_seq, title, category, push_at,
       n_sent, n_open,
       ROUND(open_rate_sent*100, 2)  AS open_pct_sent,
       n_reached,
       ROUND(open_rate_reach*100, 2) AS open_pct_reach,
       updated_at
FROM PROD_GRIP_DW.DATA_ANAL.PUSH_RESULT
WHERE n_sent > 0                                  -- 발송모수 0/NULL 제외(방어)
ORDER BY push_at DESC NULLS LAST;

-- 3b) 대시보드용 집계 뷰 (Snowsight 타일 소스) --------------------------------
CREATE OR REPLACE VIEW PROD_GRIP_DW.DATA_ANAL.V_PUSH_RESULT_BY_CATEGORY AS
SELECT category,
       COUNT(*)                                        AS n_push,
       SUM(n_sent)                                     AS total_sent,
       SUM(n_open)                                     AS total_open,
       ROUND(SUM(n_open)/NULLIF(SUM(n_sent),0)*100, 2) AS open_pct_pooled,  -- 카테고리 통합 오픈율
       ROUND(AVG(open_rate_sent)*100, 2)               AS open_pct_avg      -- 푸시별 오픈율 단순평균
FROM PROD_GRIP_DW.DATA_ANAL.PUSH_RESULT
WHERE n_sent > 0
GROUP BY category
ORDER BY open_pct_pooled DESC;

CREATE OR REPLACE VIEW PROD_GRIP_DW.DATA_ANAL.V_PUSH_RESULT_DAILY AS
SELECT TO_DATE(push_at)                                AS push_date,
       COUNT(*)                                        AS n_push,
       SUM(n_sent)                                     AS total_sent,
       SUM(n_open)                                     AS total_open,
       ROUND(SUM(n_open)/NULLIF(SUM(n_sent),0)*100, 2) AS open_pct_pooled
FROM PROD_GRIP_DW.DATA_ANAL.PUSH_RESULT
WHERE n_sent > 0
GROUP BY TO_DATE(push_at)
ORDER BY push_date;

-- =============================================================================
-- Snowsight Workspaces / Worksheet 실행 가이드
-- =============================================================================
-- ▷ 위 CREATE 문(테이블·프로시저·뷰)은 2026-06-02 이미 생성 완료.
--   평소엔 다시 실행할 필요 없음 — 이 파일은 기록/재생성/이관용.
--   재실행해도 안전: CREATE TABLE IF NOT EXISTS = 기존 데이터(6988 등) 보존,
--   프로시저·뷰는 CREATE OR REPLACE = 같은 정의로 덮어쓰기만(데이터 영향 없음).
--
-- ▷ 워크시트 우측 상단 컨텍스트만 맞추면 됨:
--     Warehouse = XS_BATCH  (실행 중이어야 CALL/SELECT 동작)
--     Role      = ACCOUNTADMIN  (또는 접근 권한 있는 역할)
--     Database/Schema = 안 골라도 됨 (모든 객체가 완전수식 PROD_GRIP_DW.DATA_ANAL...)
--
-- ▷ 이 파일 전체를 한 번에 돌릴 땐 프로시저 본문 $$...$$ 때문에
--     "전체 선택 → Run All" 로 실행 (한 줄씩 실행 X).
--
-- ── 운영(일상): 아래 두 줄이 전부 ───────────────────────────────────────────
-- 결과 추가/갱신 (발송 2~3일 뒤 push_seq 입력. 같은 seq 재실행하면 갱신):
--     CALL PROD_GRIP_DW.DATA_ANAL.SP_PUSH_RESULT_UPSERT('7100');            -- 단건
--     CALL PROD_GRIP_DW.DATA_ANAL.SP_PUSH_RESULT_UPSERT('7100,7101,7102');  -- 복수
-- 조회 (대시보드 소스):
--     SELECT * FROM PROD_GRIP_DW.DATA_ANAL.V_PUSH_RESULT;
--
-- ── (선택) 과거분 백필: 최근 60일 중 '발송 2일 지난' 푸시 일괄 적재 ─────────
--     CALL PROD_GRIP_DW.DATA_ANAL.SP_PUSH_RESULT_UPSERT(
--       (SELECT LISTAGG(DISTINCT push_seq, ',')
--          FROM PROD_GRIP_DW.DATA_ANAL.PUSH_SEND_HISTORY
--         WHERE send_status='Y'
--           AND push_at >= DATEADD('day',-60,CURRENT_TIMESTAMP())
--           AND push_at <  DATEADD('day', -2,CURRENT_TIMESTAMP())) );
--
-- ── (선택) 자동화: 위 프로시저를 Task로 감싸 스케줄 ────────────────────────
--   CREATE OR REPLACE TASK PROD_GRIP_DW.DATA_ANAL.T_PUSH_RESULT
--     WAREHOUSE = XS_BATCH
--     SCHEDULE  = 'USING CRON 0 8 * * * Asia/Seoul'   -- 매일 08:00 KST
--   AS
--     CALL PROD_GRIP_DW.DATA_ANAL.SP_PUSH_RESULT_UPSERT(
--       (SELECT LISTAGG(DISTINCT push_seq, ',')
--         FROM PROD_GRIP_DW.DATA_ANAL.PUSH_SEND_HISTORY
--        WHERE send_status='Y' AND push_at >= DATEADD('day',-7,CURRENT_TIMESTAMP())) );
--   ALTER TASK PROD_GRIP_DW.DATA_ANAL.T_PUSH_RESULT RESUME;
-- =============================================================================

-- =============================================================================
-- 카테고리 매핑 스냅샷 (2026-06-04) — 수동 category 복구용 (재실행 가능)
-- =============================================================================
-- category는 수동값이라 CALL/백필로 복원 안 됨. 테이블 재생성·유실 시 아래로 복구.
-- 새 푸시 분류 시 VALUES 목록에 (push_seq,'카테고리') 한 줄 추가하면 됨.
-- (비상품 푸시 6959/7063/7076/7028/7044 등은 카테고리 없이 행 제거됨)
--   UPDATE PROD_GRIP_DW.DATA_ANAL.PUSH_RESULT t SET category = m.cat
--   FROM (VALUES
--     (6971,'주얼리'),(6973,'식품'),(6974,'생활/주방'),(6975,'패션잡화'),(6977,'뷰티'),
--     (6986,'패션의류'),(6987,'주얼리'),(6988,'뷰티'),(6989,'주얼리'),(6990,'패션의류'),
--     (7000,'식품'),(7002,'패션의류'),(7008,'식품'),(7010,'패션의류'),(7014,'뷰티'),
--     (7015,'패션의류'),(7019,'패션잡화'),(7020,'뷰티'),(7029,'뷰티'),(7030,'식품'),
--     (7031,'뷰티'),(7032,'뷰티'),(7033,'패션잡화'),(7038,'식품'),(7039,'식품'),
--     (7040,'식품'),(7042,'식품'),(7043,'디지털/가전'),(7045,'패션잡화'),(7052,'패션잡화'),
--     (7053,'디지털/가전'),(7056,'디지털/가전'),(7057,'패션의류'),(7058,'패션잡화'),(7059,'생활/주방'),
--     (7060,'식품'),(7061,'뷰티'),(7068,'식품'),(7069,'디지털/가전'),(7070,'뷰티'),
--     (7077,'패션잡화'),(7084,'식품'),(7086,'뷰티')
--   ) AS m(seq,cat) WHERE t.push_seq = m.seq;
-- =============================================================================
