# grip-aidata-push — 푸시 마케팅 효율화

라이브 커머스 푸시 알림을 **전체 발송 대신 "반응할 세그먼트"에만** 보내기 위한 운영 도구.
유저를 행동 기반 k-prototypes 클러스터(10개)로 나누고, 클러스터별 과거 반응을 근거로
새 푸시를 어느 세그먼트에 보낼지 배분한다. 같은 클릭 수를 더 적은 발송으로 달성하는 것이 목표.

- **백엔드** FastAPI (`backend/`, :8010) — Snowflake 집계 + 클러스터 스냅샷/분배 API
- **프론트** React 19 + Vite 7 + TS + Tailwind (`frontend/`, :3001)
- **공유 코어** `core/` — 클러스터링·분배 로직 (백엔드와 분석 노트북이 공유)
- **데이터** Snowflake (key-pair 인증). 모델·캐시·비밀은 repo/이미지에 **포함하지 않고** 호스트에 둔다.

운영자용 화면 사용법은 [`대시보드_이용가이드.md`](대시보드_이용가이드.md) 참고. 이 문서는 구조·방법론·배포에 집중한다.

---

## 1. 화면 (3탭)

| 탭 | 하는 일 | 주요 데이터 |
|---|---|---|
| **푸시 현황** | 발송된 푸시의 도달·오픈·전환을 본다. ① 푸시별 오픈율 표/추이 ② **세그먼트(클러스터)별 전환** ③ **메시지 세그먼트별 커버리지** + 오픈 후 방송 자동 매핑(판매자명·push_seq로 방송 발견) | `data_anal.push_result`, 수기 매핑 `cache/push_content_id_map.json` |
| **유저 클러스터** | [최신화] → 발송가능 전체 모수를 현재 시점 피처로 10개 클러스터에 배정. 클러스터별 규모·5월 오픈율·발송 가이드를 한 표로 | `cache/latest_cluster.csv` (회당 Snowflake 약 3~15분) |
| **푸시 분배** | 보낼 푸시 N개(카테고리·시간대)를 입력 → 우선순위 높은 세그먼트부터 비교우위로 배분 → 푸시별 발송 대상 `user_id` CSV + 전체발송 대비 효율(클릭 커버리지·발송 절감·오픈율 배율). A/B 홀드아웃(대조군) 분리 옵션 | `latest_cluster.csv` + `cluster_push_stats.pkl` |

---

## 2. 아키텍처

```
backend/                         FastAPI (인증 없음 — 사내망/컨테이너 전용)
  app/
    main.py                      앱 생성 · /health · sys.path 에 repo 루트 추가(core import)
    routes/v1/                   meta · push_results · cluster_snapshot · allocations · jobs · ab_test  (prefix /api/v1)
    services/                    push_results · push_mapping · push_funnel · segment_conversion
                                 · allocation_svc · ab_test(A/B 효과검증) · snapshot · jobs(단일실행 락)
    models/responses.py          Pydantic 요청/응답 모델 (frontend/src/types/push.ts 와 1:1 동기화)
  pyproject.toml · uv.lock       의존성 (uv)

frontend/
  src/pages/                     PushResultsPage · ClusterSnapshotPage · AllocationPage
  src/components/                AbTestSection(A/B 효과검증) · SegmentShareChart
  src/services/pushService.ts    axios API 클라이언트
  src/types/push.ts              백엔드 응답 타입
  vite.config.ts                 dev 프록시 /api → env API_TARGET (기본 :8000, 운영/로컬 dev 는 :8010 주입)

core/                            백엔드와 분석 노트북이 공유하는 순수 로직
  datasources/snowflake_kp.py    key-pair 인증 · run_snowflake/run_ddl(전역 단일 세션·temp-table용, 스레드 비안전→job Lock) · run_query(호출마다 새 연결, 스레드 안전)
  push/
    audience.py                  발송가능 모수 SQL (마케팅 표준 쿼리)
    features.py                  현재 시점 피처 적재 + 스냅샷 갱신 (temp-table 파이프라인)
    clustering.py                전처리 + k-prototypes predict (노트북과 동일 로직)
    artifacts.py                 모델/통계 pkl lazy 로드 · MAY_ORDER · RANK
    descriptions.py              클러스터 별칭·Tier·설명·액션 가이드 (정적 큐레이션)
    allocation.py                다중 푸시 분배 LP (비교우위 + 수송 문제)
    paths.py                     repo 절대경로 상수

artifacts/   (gitignore)         모델: kp_artifacts.pkl · cluster_push_stats.pkl
cache/       (gitignore)         latest_cluster.csv · latest_cluster_meta.json · push_content_id_map.json · allocations/*.csv
analysis/    (gitignore)         분석 노트북(*.ipynb) · 조사 스크립트 — 로컬 보관, 미추적
.env         (gitignore)         Snowflake 접속값 (템플릿: .env.example)
```

코어 로직은 분석 노트북에서 추출해 백엔드와 공유한다. `clustering.py` 의 전처리는
클러스터 학습 노트북과 **동일 로직이어야** 하므로 한쪽을 고치면 양쪽을 맞춰야 한다.

---

## 3. 클러스터링 방법론

분석 노트북은 공개 대상이 아니므로(자격증명·PII 포함) 방법론을 여기에 정리한다.

### 3.1 모수 (audience)
마케팅팀 표준 쿼리(`core/push/audience.py`)를 채용한다.

- 최근 N일 내 **라이브 시청 이력**(`view_media_log.media_type = 3`)이 있고
- **유효 회원**(`user_status <> 3`, `user_type IN (2,5)`)이며
- **마케팅 수신 동의**(`settings.marketing_push = 'Y'`)이고 제외번호(`grip_db.exclude`)가 아닌 유저

유저당 1행을 보장(EXISTS/NOT EXISTS 로 fan-out 차단). 최근 스냅샷(2026-06-05, n_day=365) 기준 **약 41.4만 명**.

### 3.2 학습 코호트
모델은 **2026-05-05 ~ 05-31, 44개 푸시, 134,862명**(5월 도달 유저 + 오픈 라벨)으로 학습됐다.
스코어링(현재 모수 배정)과 학습(5월 라벨)의 시점이 달라 covariate shift 가 존재한다(§3.6).

### 3.3 피처 (7종)
| 종류 | 피처 | 정의 |
|---|---|---|
| 수치 | `REC_LOG_Z` | 최근 방문 경과일(`RECENCY_DAYS`, 결측=400). `log1p` 후 z-score |
| 수치 | `ORD_LOG_Z` | 최근 3개월 구매 건수(`ORDER_COUNT_3M`). `log1p` 후 z-score |
| 범주 | `GRADE` | 회원 등급 (결측=`'10'`) |
| 범주 | `PRIMARY_HOUR_BUCKET` | 주 활동 시간대 — dawn(0–5)/morning(6–11)/afternoon(12–17)/evening(18–23), 로그 없으면 `9_no_log` |
| 범주 | `WATCH_CAT` | 최다 시청(≥10초) 방송 카테고리 |
| 범주 | `CLICK_CAT` | 최다 클릭 상품 카테고리 |
| 범주 | `ORDER_CAT` | 최다 구매 상품 카테고리 |

범주형은 학습 시 **카테고리별 상위 빈도(top-8)** 만 남기고 나머지는 `기타`, 결측은 `없음` 으로 묶는다.
행동 피처는 events_all / elasticsearch / order_all 의 **최근 3개월**, recency 는 최근 1년 기준.

### 3.4 모델
- **KPrototypes (k=10)** — `kmodes` 패키지, Cao 초기화. 수치 2 + 범주 5(`CAT_IDX=[2,3,4,5,6]`).
- 수치는 표준화, 범주는 Hamming 거리(γ 자동). 대용량 배정은 10만 행 청크 단위 predict.
- 산출물:
  - `kp_artifacts.pkl` — 모델 + 전처리 파라미터(`top_cats`, `z_stats`) + 컬럼 정의 + 설명
  - `cluster_push_stats.pkl` — 5월 실측 **클러스터 × 푸시카테고리 × 발송시간대** 의 (노출 `n`, 오픈 `opens`)
- **k=10 선택 근거**: 클러스터 간 오픈율 분리도가 뚜렷하고, 각 군 표본이 충분(≥ 모수 2%)하며,
  운영자가 설명 가능한(VIP~휴면) 단위로 나뉘는 지점.

### 3.5 세그먼트 프로필 (S1~S10)
모델 원본 라벨(C0~C9)은 임의값이라 직관성이 없어, **5월 전체 오픈율(lift) 순으로 발송 우선순위
1~10(S1~S10)** 을 부여한다(`MAY_ORDER`, 고정 — 새 푸시 성적으로 재정렬 금지).
전체 평균 5월 오픈율 = **1.241%**.

| 순위 | 원본 | 별칭 | Tier | 5월 오픈율 | lift | 발송 가이드 |
|---|---|---|---|---|---|---|
| S1 | C9 | VIP 코어 | 🟢 상시 | 2.87% | 2.31× | 어떤 카테고리든 가장 먼저 보내는 1순위 |
| S2 | C0 | 우대 헤비 | 🟢 상시 | 2.34% | 1.88× | 거의 모든 푸시에 기본 포함 |
| S3 | C3 | 일반 구매자 | 🟢 상시 | 2.09% | 1.68× | 식품 우선, 다른 카테고리도 폭넓게 |
| S4 | C4 | 최근 활성 | 🟡 조건부 | 1.48% | 1.19× | 카테고리 맞을 때(주얼리·패션) 선별 |
| S5 | C7 | 주간 활성 | 🟡 조건부 | 1.39% | 1.12× | 무구매 잠재층 — 식품·뷰티 첫 구매 전환 |
| S6 | C8 | 잠재 활성 | 🔴 제외/실험 | 1.02% | 0.82× | 오전·주얼리 반응 실험 그룹 |
| S7 | C6 | 월간 둔화 | 🔴 제외/실험 | 0.86% | 0.69× | 휴면 직전 — 발송 축소(주 1회↓) |
| S8 | C1 | 라이트 유저 | 🔴 제외/실험 | 0.72% | 0.58× | 반응 낮음 — 주 1회 제한 |
| S9 | C2 | 비활성 | 🔴 제외/실험 | 0.65% | 0.52× | 재활성화 캠페인 외 제외 |
| S10 | C5 | 휴면 | 🔴 제외/실험 | 0.63% | 0.51× | 일반 푸시 제외 — 파격할인·복귀쿠폰 같은 공격적 캠페인만 |

Tier1(S1~S3)이 평균의 1.7~2.3배로 반응한다. 분배는 우선순위 상위 `top_k`(기본 5) 풀에서 시작한다.

### 3.6 한계 — covariate shift
- 모델은 **5월 활성 발송 유저**로 학습됐는데, 스코어링 모수는 **최근 1년 시청자 41만 명**으로 더 넓다.
  그 결과 최신 스냅샷(2026-06-05)에서는 약 **70%(28.9만)가 S10 휴면**에 몰린다(Tier1은 ~6.8%).
- 따라서 분배 화면의 **기대 오픈수·오픈율은 5월 in-sample 추정치**다 — 절대값이 아니라 세그먼트 간
  상대 비교·우선순위 근거로 사용하고, **실효과는 무작위 홀드아웃(A/B)으로 검증**한다.
- 모델 재학습 시 `descriptions.py`(별칭·Tier·설명·액션)와 이 표를 함께 갱신해야 한다.

---

## 4. 데이터 파이프라인

| 단계 | 무엇 | 코드 / 산출물 |
|---|---|---|
| 모수 | 발송가능 유저 | `audience.py` → temp `_coh` |
| 피처 | 현재 시점 7종 피처 (events_all·elasticsearch·order_all 3개월, recency 1년) | `features.load_features_now` |
| 스냅샷 | 피처 → 클러스터 배정 → 저장 | `features.refresh_snapshot` → `cache/latest_cluster.csv` + `_meta.json` (회당 3~15분, 단일 실행 락) |
| 푸시 현황 | 발송·도달·오픈 집계 | `push_results.py` ← `data_anal.push_result` |
| 세그먼트 전환 | push→방송(content) **수기 확정 매핑** + 시간순서(발송 이후) 로 오픈→유효시청(≥10s)→구매 집계 | `segment_conversion.py` + `cache/push_content_id_map.json` |
| 방송 자동 발견 | push_seq + 판매자명 → 발송시각 전후([-2h,+3h]) 방송 발견 + 펀넬 미리보기 | `push_mapping.py` · `push_funnel.py` |
| 분배 | 비교우위(카테고리 메인효과 제거) + 수송 LP(`scipy.linprog`)로 세그먼트→푸시 배분, 도달률 환산 | `allocation.py` |

주요 Snowflake 테이블: `default.events_all`(오픈·클릭), `default.elasticsearch`(시청),
`default.order_all`(구매), `data_anal.push_send_history`(발송), `data_anal.push_result`(현황),
`grip_db.push`(푸시 메타), `grip_db_realtime.content`/`member`(방송·회원).

---

## 5. 실행

### (A) Docker on gpu2 — 권장 배포

이미지엔 **코드만** 들어가고, 데이터·모델·비밀은 호스트 볼륨으로 마운트한다.

**사전 준비 (호스트)**
1. `.env.example` 를 `.env` 로 복사하고 Snowflake 값을 채운다.
2. Snowflake **개인키(.p8, 무암호 PKCS#8)** 를 호스트에 둔다(기본 `~/.snowflake/snowflake.p8`,
   다른 경로면 `SNOWFLAKE_KEY_HOST_PATH` 환경변수로 지정).
3. `artifacts/`(모델 pkl), `cache/`(스냅샷·매핑)가 repo 루트에 있어야 한다. 없으면 클러스터 화면에서 먼저 [최신화].

**기동**
```bash
docker compose build
docker compose up -d
```
- 대시보드: `http://<gpu2-host>:3001/`
- API docs: `http://<gpu2-host>:8010/docs`
- 프론트(nginx)가 `/api` 를 backend 컨테이너(:8010)로 프록시한다.

포트는 **8010·3001** 로, gpu2 기존 점유(8000/3000/8080/8888 등)와 충돌을 피한다.
`restart: unless-stopped` 지만 부팅 자동 복귀는 별도 설정 필요.

### (B) 로컬 dev
```bash
# 백엔드 — http://localhost:8010  (/docs 에 Swagger)
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8010

# 프론트 — http://localhost:3001  (/api 는 :8010 으로 프록시)
cd frontend && npm install
FRONTEND_PORT=3001 API_TARGET=http://localhost:8010 npm run dev -- --host
```
전제: `~/.snowflake/snowflake.p8` + 루트 `.env`. 기동 헬퍼는 [`start_dashboard.sh`](start_dashboard.sh) 참고.

---

## 6. 비밀 / 공개 정책

repo·이미지에 **올리지 않는다**(`.gitignore`·`.dockerignore` 로 차단, 직접 준비 필요):
- `.env`, `*.p8`/`*.pem`/`*.key` — 접속 정보·개인키
- `cache/`, `artifacts/` — USER_SEQ/USER_ID 단위 데이터·학습 모델
- `analysis/`, `*.ipynb` — 분석 노트북(로컬 보관)

커밋되는 것: 소스 코드, `.env.example`, `push_result_setup.sql`(DB 셋업 참고), 문서.

> 참고: 게시 시 이 디렉토리를 독립 repo 로 만들려면 `cd grip-aidata-push && git init` 후
> 커밋하면 루트 `.gitignore` 가 그대로 적용된다.
