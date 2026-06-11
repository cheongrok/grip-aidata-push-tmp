"""API 요청/응답 모델 — frontend/src/types/push.ts 와 동기화 (요청: DiscoverReq·ContentMapReq·AllocationReq·ABVerifyReq)."""

from typing import Literal

from pydantic import BaseModel, Field


# ── 페이지 ① 푸시 현황 ──
class PushResultRow(BaseModel):
    push_seq: int
    title: str | None
    push_at: str | None
    n_sent: int | None
    n_reached: int | None
    n_open: int | None
    open_rate_sent: float | None
    open_rate_reach: float | None
    category: str | None
    updated_at: str | None


class PushResultsRes(BaseModel):
    rows: list[PushResultRow]


# ── 오픈→유효시청→구매 펀넬 (discover-content 응답의 미리보기에 포함) ──
class PushFunnelContentRow(BaseModel):
    content_seq: int
    n_valid_watch: int
    n_purchase: int


class PushFunnelRes(BaseModel):
    push_seq: int
    n_open: int            # 오픈 유저 수 (push_at 이후)
    n_valid_watch: int     # 그중 지정 content 유효시청(>=10s) 유저 (distinct)
    n_purchase: int        # 그중 지정 content 구매 유저 (distinct)
    by_content: list[PushFunnelContentRow] | None = None


# ── 푸시 방송 자동 매핑 (push_seq + 판매자이름 → content 발견 → 커버리지 매핑 저장) ──
class DiscoverReq(BaseModel):
    push_seq: int = Field(ge=1)
    user_name: str


class DiscoverContentRow(BaseModel):
    content_seq: int
    content_id: str
    title: str | None = None
    created_at: str | None = None
    view_count: int = 0


class DiscoverRes(BaseModel):
    push_seq: int
    push_at: str | None = None
    title: str | None = None
    n_sent: int = 0                      # 발송수 (send_status=Y) — 0이면 미발송 경고
    seller_name: str = ""
    seller_matches: int = 0              # user_name 일치 회원 수 (>1이면 동명이인 경고)
    contents: list[DiscoverContentRow] = []
    funnel: PushFunnelRes | None = None  # 오픈→유효시청→구매 미리보기
    warnings: list[str] = []             # 비어있으면 저장 가능


class ContentMapReq(BaseModel):
    push_seq: int = Field(ge=1)
    content_ids: list[str]
    category: str
    seller_name: str = ""
    title: str | None = None


class ContentMapRes(BaseModel):
    ok: bool
    push_seq: int


# ── 페이지 ② 클러스터 스냅샷 ──
class ClusterCard(BaseModel):
    cluster: int          # 모델 원본 라벨 (0~9) — 추적용
    rank: int             # 표시 번호 (1~10, 발송 우선순위 순)
    short_name: str = ""  # 짧은 별칭 (예: "VIP 코어") — 화면 "S{rank} {short_name}"
    tier: str
    tier_label: str
    short_desc: str
    friendly_desc: str
    action: str = ""      # "어떻게 쓰면 좋을지" 한 문장 액션 가이드 (특징 칸 끝 볼드)
    count: int
    share_pct: float
    may_ctr_pct: float
    may_lift: float


class SnapshotRes(BaseModel):
    snapshot_at: str
    refreshed_at: str
    n_day: int
    n_users: int
    duration_sec: float
    stale: bool          # 7일 초과
    clusters: list[ClusterCard]


class JobRes(BaseModel):
    job_id: str
    kind: str
    status: Literal["running", "done", "error"]
    stage: str | None = None
    error: str | None = None


# ── 페이지 ③ 분배 ──
class PushInput(BaseModel):
    title: str = ""
    category: str
    hour_bucket: str | None = None
    clusters: list[int] | None = None  # 수동 타겟: 이 푸시가 갈 클러스터 원본 id. None/빈값=자동(top_k)


class AllocationReq(BaseModel):
    pushes: list[PushInput]
    top_k: int = Field(default=5, ge=1, le=10)
    volumes: list[float] | None = None
    seed: int = 42
    holdout_pct: float = Field(default=0.0, ge=0, le=0.5)  # A/B 대조군 비율 — 발송에서 무작위로 빼둠(0=홀드아웃 없음)


class AllocCell(BaseModel):
    count: int
    share_pct: float
    expected_ctr_pct: float
    fallback_level: int  # 0=카테고리×시간대, 1=카테고리만, 2=전체 폴백


class AllocMatrixRow(BaseModel):
    cluster: int          # 모델 원본 라벨 (0~9) — 추적용
    rank: int             # 표시 번호 (1~10, 발송 우선순위 순)
    short_name: str = ""  # 짧은 별칭 (예: "VIP 코어")
    desc: str
    size: int
    allocation: dict[int, AllocCell]


class AllocPerPush(BaseModel):
    title: str
    category: str
    hour_bucket: str | None
    target_sends: int
    expected_opens: int
    expected_open_rate_pct: float
    full_sends: int
    full_expected_opens: int
    full_open_rate_pct: float
    click_coverage_pct: float | None
    send_reduction_pct: float
    ctr_multiplier: float | None
    download_url: str
    csv_rows: int
    holdout_rows: int = 0                     # A/B 대조군(미발송) 인원
    control_download_url: str | None = None   # 대조군 CSV (홀드아웃 있을 때만)


class AllocTotals(BaseModel):
    total_users: int
    target_sends: int
    send_pct: float
    expected_opens: int
    full_expected_opens: int
    click_coverage_pct: float | None


class AllocationRes(BaseModel):
    run_id: str
    snapshot_at: str
    pool: list[int]
    matrix: list[AllocMatrixRow]
    per_push: list[AllocPerPush]
    totals: AllocTotals
    delivery_rate: float = 1.0       # 발송 대비 환산용 도달률(reached/sent). 기대오픈 = 도달대비CTR × 이 값
    delivery_basis: str = ""         # 도달률 산출 근거 (예: "push_result 최근 90일" / "fallback")


# ── 세그먼트(클러스터)별 전환율 ──
class SegmentStats(BaseModel):
    n_sent: int = 0       # 발송 (push_send_history, send_status=Y·token_valid=Y)
    n_reached: int = 0    # 도달 (events_all 푸시 라벨 수신 distinct)
    n_open: int
    n_view: int
    n_purchase: int
    view_rate_pct: float      # n_view / n_open * 100 (유효시청률, 오픈자 기준)
    purchase_rate_pct: float  # n_purchase / n_open * 100 (거래전환율, 오픈자 기준)
    gmv_sum: int = 0          # 전환 구매 GMV 합 (원). 옛 캐시 호환 위해 기본 0
    aov: int = 0              # 객단가 = gmv_sum / n_purchase (구매자 1인당, 원)


class SegmentClusterStats(SegmentStats):
    cluster: int           # -1 = 미배정(스냅샷 외)
    rank: int | None = None  # 표시 번호 (1~10, 발송 우선순위 순). 미배정(-1)은 None
    short_name: str = ""     # 짧은 별칭 (예: "VIP 코어"). 미배정은 ""
    desc: str


class SegmentPushRow(BaseModel):
    push_seq: int
    title: str | None
    category: str | None
    push_at: str | None
    seller_name: str = ""         # 판매자(방송 소유자) 이름. 복수 방송이면 ", "로 결합
    content_ids: list[str] = []   # 수기 확정 방송 content_id (복수 가능)
    content_seqs: list[int] = []  # 위 content_id 의 숫자 content_seq
    overall: SegmentStats
    clusters: list[SegmentClusterStats]


# 최근 방송 종합 — 세그먼트별 오픈자 vs 비오픈자 비교 (발송 모수 내) — 시청·구매율
class OpenCompareRow(BaseModel):
    cluster: int                             # 모델 원본 라벨 (-1=미배정)
    rank: int | None = None                  # 표시 번호 (S1~S10). 미배정은 None
    short_name: str = ""                     # 짧은 별칭 (예: "VIP 코어")
    n_sent: int = 0                          # 발송(누적, 최근 N방송 합산)
    n_open: int = 0                          # 그중 오픈
    open_rate_pct: float = 0.0               # 오픈/발송
    opener_view_rate_pct: float = 0.0        # 오픈자 유효시청률
    opener_purchase_rate_pct: float = 0.0    # 오픈자 구매전환율
    nonopener_view_rate_pct: float = 0.0     # 비오픈자(발송됐으나 미오픈) 유효시청률
    nonopener_purchase_rate_pct: float = 0.0 # 비오픈자 구매전환율


class ClusterUserSampleRow(BaseModel):
    user_seq: int
    user_id: str
    user_name: str = ""
    grade: int            # member_grade.grade (무등급=10), 높을수록 상위
    gender: str = ""      # M/F/X/""


class SegmentConversionRes(BaseModel):
    computed_at: str
    cluster_snapshot_at: str
    period_start: str
    period_end: str
    n_pushes: int
    mapping: str = "manual"  # 매핑 방식 (manual=수기 확정)
    totals: SegmentStats
    by_cluster_total: list[SegmentClusterStats]
    pushes: list[SegmentPushRow]
    open_compare: list[OpenCompareRow] = []  # 최근 방송 종합 세그먼트별 오픈/비오픈 비교 (기본 [], 옛 캐시 호환)
    open_compare_n_pushes: int = 0           # open_compare 에 합산된 최근 방송 수
    cluster_user_samples: dict[str, list[ClusterUserSampleRow]] = {}  # 원본 cluster id "0".."9" → 오픈자 샘플(등급순)


# ── 클러스터 내 유저 샘플 분석 (선택 유저 프로필) ──
class WatchedBroadcast(BaseModel):
    seller: str = ""
    title: str = ""
    watch_sec: int               # 최근 90일 누적 시청 초 (프론트에서 분·초로 표시)


class ClusterUserProfileRes(BaseModel):
    user_seq: int
    user_id: str
    user_name: str = ""
    grade: int
    gender: str | None = None     # M/F/X/None
    age: int | None = None        # BIRTH 연도 기반, NULL/파싱실패 시 None
    total_spend: int              # 최근 90일 SUM(gmv WHERE cancel_at IS NULL AND gmv>0)
    purchase_count: int           # 최근 90일 COUNT(DISTINCT order_seq)
    aov: int                      # 총지출 / 구매횟수
    top_broadcasts: list[WatchedBroadcast] = []  # 최근 90일 시청시간 상위 방송


# ── 푸시알림 효과검증 (A/B 홀드아웃) ──
class ABVerifyReq(BaseModel):
    push_seq: int = Field(ge=1)
    content_seqs: list[int]                 # 푸시가 홍보한 방송/상품 content_seq
    days: int | None = Field(default=None, ge=1, le=60)  # 측정기간(발송시각+N일). None=현재까지
    treatment_user_seqs: list[int]          # 발송군 (CSV USER_SEQ)
    treatment_clusters: list[int] = []      # 발송군 CLUSTER (길이 같으면 세그먼트별 분해)
    control_user_seqs: list[int]            # 대조군 (미발송)
    control_clusters: list[int] = []


class ABArm(BaseModel):
    n_users: int
    n_watch: int
    n_purchase: int
    gmv_sum: int
    watch_rate_pct: float
    purchase_rate_pct: float
    gmv_per_user: int
    aov: int


class ABMetric(BaseModel):
    treatment_pct: float
    control_pct: float
    lift_pp: float              # 증분 = 발송 − 대조 (%포인트)
    z: float | None = None
    p_value: float | None = None
    significant: bool = False   # p < 0.05


class ABClusterRow(BaseModel):
    cluster: int
    rank: int | None = None
    short_name: str = ""
    t_users: int
    c_users: int
    t_purchase_rate_pct: float
    c_purchase_rate_pct: float
    lift_pp: float
    p_value: float | None = None


class ABVerifyRes(BaseModel):
    push_seq: int
    push_at: str | None
    content_seqs: list[int]
    period_start: str
    period_end: str
    treatment: ABArm
    control: ABArm
    watch: ABMetric            # 유효시청전환 증분
    purchase: ABMetric         # 구매전환 증분
    gmv_per_user_treatment: int
    gmv_per_user_control: int
    gmv_per_user_lift: int
    by_cluster: list[ABClusterRow]
    warnings: list[str] = []


# ── 공통 메타 ──
class SnapshotStatus(BaseModel):
    exists: bool
    snapshot_at: str | None = None
    n_users: int | None = None
    stale: bool | None = None


class MetaRes(BaseModel):
    categories: list[str]
    hour_buckets: list[str]
    may_order: list[int]
    snapshot: SnapshotStatus
