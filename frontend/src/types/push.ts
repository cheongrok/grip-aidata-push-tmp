// backend/app/models/responses.py 와 동기화

export interface PushResultRow {
  push_seq: number
  title: string | null
  push_at: string | null
  n_sent: number | null
  n_reached: number | null
  n_open: number | null
  open_rate_sent: number | null // 비율 (0.0146 = 1.46%)
  open_rate_reach: number | null
  category: string | null
  updated_at: string | null
}

export interface ClusterCard {
  cluster: number // 모델 원본 라벨 (0~9) — 추적용
  rank: number // 표시 번호 (1~10, 발송 우선순위 순)
  short_name: string // 짧은 별칭 (예: "VIP 코어") — 표기 "S{rank} {short_name}"
  tier: string
  tier_label: string
  short_desc: string
  friendly_desc: string
  action: string // "어떻게 쓰면 좋을지" 한 문장 액션 가이드
  count: number
  share_pct: number
  may_ctr_pct: number
  may_lift: number
}

export interface SnapshotRes {
  snapshot_at: string
  refreshed_at: string
  n_day: number
  n_users: number
  duration_sec: number
  stale: boolean
  clusters: ClusterCard[]
}

export interface JobRes {
  job_id: string
  kind: string
  status: 'running' | 'done' | 'error'
  stage: string | null
  error: string | null
}

export interface PushInput {
  title?: string // 메모용 (BE 기본값 ""), 생략 가능
  category: string
  hour_bucket: string | null
  clusters?: number[] | null // 수동 타겟: 이 푸시가 갈 클러스터 원본 id. 없으면 자동(top_k)
}

export interface AllocCell {
  count: number
  share_pct: number
  expected_ctr_pct: number
  fallback_level: number
}

export interface AllocMatrixRow {
  cluster: number // 모델 원본 라벨 (0~9) — 추적용
  rank: number // 표시 번호 (1~10, 발송 우선순위 순)
  short_name: string // 짧은 별칭 (예: "VIP 코어")
  desc: string
  size: number
  allocation: Record<number, AllocCell>
}

export interface AllocPerPush {
  title: string
  category: string
  hour_bucket: string | null
  target_sends: number
  expected_opens: number
  expected_open_rate_pct: number
  full_sends: number
  full_expected_opens: number
  full_open_rate_pct: number
  click_coverage_pct: number | null
  send_reduction_pct: number
  ctr_multiplier: number | null
  download_url: string
  csv_rows: number
  holdout_rows: number // A/B 대조군(미발송) 인원
  control_download_url: string | null // 대조군 CSV (홀드아웃 있을 때만)
}

export interface AllocationRes {
  run_id: string
  snapshot_at: string
  pool: number[]
  matrix: AllocMatrixRow[]
  per_push: AllocPerPush[]
  totals: {
    total_users: number
    target_sends: number
    send_pct: number
    expected_opens: number
    full_expected_opens: number
    click_coverage_pct: number | null
  }
  delivery_rate?: number // 발송 대비 환산 도달률(reached/sent) — 기대오픈에 반영됨
  delivery_basis?: string // 도달률 산출 근거
}

export interface PushFunnelContentRow {
  content_seq: number
  n_valid_watch: number
  n_purchase: number
}

// 오픈 유저 기준 전환 퍼널 — push_seq + content_seq(복수) 입력 (push_at 이후 기준)
export interface PushFunnelRes {
  push_seq: number
  n_open: number // 오픈 유저 수
  n_valid_watch: number // 그중 유효시청 유저 수
  n_purchase: number // 그중 구매 유저 수
  by_content: PushFunnelContentRow[] | null // content_seq별 분해 (백엔드 선택 제공)
}

// 푸시 방송 자동 매핑 — push_seq + 판매자이름 → 방송 발견
export interface DiscoverContentRow {
  content_seq: number
  content_id: string
  title: string | null
  created_at: string | null
  view_count: number
}

export interface DiscoverRes {
  push_seq: number
  push_at: string | null
  title: string | null
  n_sent: number // 발송수 (0이면 미발송 경고)
  seller_name: string
  seller_matches: number // user_name 일치 수 (>1이면 동명이인 경고)
  contents: DiscoverContentRow[]
  funnel: PushFunnelRes | null // 오픈→유효시청→구매 미리보기
  warnings: string[] // 비어있고 contents>0·n_sent>0 이면 저장 가능
}

// 세그먼트별 전환 (cache/segment_conversion.json 계약과 동기화)
export interface SegmentStats {
  n_sent: number // 발송 (push_send_history, send_status=Y·token_valid=Y)
  n_reached: number // 도달 (events_all 푸시 라벨 수신 distinct)
  n_open: number
  n_view: number
  n_purchase: number
  view_rate_pct: number // n_view / n_open * 100 (유효시청률, 오픈자 기준)
  purchase_rate_pct: number // n_purchase / n_open * 100 (거래전환율, 오픈자 기준)
  gmv_sum: number // 전환 구매 GMV 합 (원)
  aov: number // 객단가 = gmv_sum / n_purchase (구매자 1인당, 원)
}

export interface SegmentClusterStats extends SegmentStats {
  cluster: number // -1 = 미배정(스냅샷 외)
  rank: number | null // 표시 번호 (1~10, 발송 우선순위 순). 미배정(-1)은 null
  short_name: string // 짧은 별칭 (예: "VIP 코어"). 미배정은 ""
  desc: string
}

export interface SegmentPushRow {
  push_seq: number
  title: string | null
  category: string | null
  push_at: string | null
  seller_name: string // 판매자(방송 소유자) 이름. 복수 방송이면 ", "로 결합
  content_ids: string[] // 수기 확정 방송 content_id (복수 가능)
  content_seqs: number[] // 위 content_id 의 숫자 content_seq
  overall: SegmentStats
  clusters: SegmentClusterStats[] // 발송 우선순위(rank 1~10) 순, 미배정 마지막, n_open=0 포함
}

// 최근 방송 종합 — 세그먼트별 오픈자 vs 비오픈자 비교 (발송 모수 내)
export interface OpenCompareRow {
  cluster: number // -1=미배정
  rank: number | null // S1~S10, 미배정은 null
  short_name: string
  n_sent: number // 발송(누적, 최근 N방송 합산)
  n_open: number
  open_rate_pct: number
  opener_view_rate_pct: number
  opener_purchase_rate_pct: number
  nonopener_view_rate_pct: number
  nonopener_purchase_rate_pct: number
}

export interface SegmentConversionRes {
  computed_at: string // 집계 시각 (isoformat)
  cluster_snapshot_at: string // latest_cluster.csv SNAPSHOT_AT 첫 값
  period_start: string
  period_end: string
  n_pushes: number
  mapping?: string // 매핑 방식 (manual=수기 확정)
  totals: SegmentStats
  by_cluster_total: SegmentClusterStats[] // cluster -1(미배정) 포함
  pushes: SegmentPushRow[]
  open_compare?: OpenCompareRow[] // 최근 방송 종합 세그먼트별 오픈/비오픈 비교 (옵셔널)
  open_compare_n_pushes?: number // open_compare 에 합산된 최근 방송 수
  cluster_user_samples?: Record<string, ClusterUserSampleRow[]> // 원본 cluster id "0".."9" → 오픈자 샘플(등급순)
}

// ── 클러스터 내 유저 샘플 분석 ──
export interface ClusterUserSampleRow {
  user_seq: number
  user_id: string
  user_name: string
  grade: number // 무등급=10, 높을수록 상위
  gender: string // M/F/X/''
}

export interface WatchedBroadcast {
  seller: string
  title: string
  watch_sec: number // 최근 90일 누적 시청 초
}

export interface ClusterUserProfileRes {
  user_seq: number
  user_id: string
  user_name: string
  grade: number
  gender: string | null
  age: number | null // BIRTH 연도 기반, 없으면 null
  total_spend: number // 최근 90일
  purchase_count: number // 최근 90일
  aov: number
  top_broadcasts: WatchedBroadcast[]
}

export interface MetaRes {
  categories: string[]
  hour_buckets: string[]
  may_order: number[]
  snapshot: { exists: boolean; snapshot_at: string | null; n_users: number | null; stale: boolean | null }
}

// 푸시알림 효과검증 (A/B 홀드아웃)
export interface AbArm {
  n_users: number
  n_watch: number
  n_purchase: number
  gmv_sum: number
  watch_rate_pct: number
  purchase_rate_pct: number
  gmv_per_user: number
  aov: number
}

export interface AbMetric {
  treatment_pct: number
  control_pct: number
  lift_pp: number // 증분 = 발송 − 대조 (%포인트)
  z: number | null
  p_value: number | null
  significant: boolean // p < 0.05
}

export interface AbClusterRow {
  cluster: number
  rank: number | null
  short_name: string
  t_users: number
  c_users: number
  t_purchase_rate_pct: number
  c_purchase_rate_pct: number
  lift_pp: number
  p_value: number | null
}

export interface AbVerifyReq {
  push_seq: number
  content_seqs: number[]
  days: number | null // 측정기간(발송+N일). null=현재까지
  treatment_user_seqs: number[]
  treatment_clusters: number[]
  control_user_seqs: number[]
  control_clusters: number[]
}

export interface AbVerifyRes {
  push_seq: number
  push_at: string | null
  content_seqs: number[]
  period_start: string
  period_end: string
  treatment: AbArm
  control: AbArm
  watch: AbMetric
  purchase: AbMetric
  gmv_per_user_treatment: number
  gmv_per_user_control: number
  gmv_per_user_lift: number
  by_cluster: AbClusterRow[]
  warnings: string[]
}
