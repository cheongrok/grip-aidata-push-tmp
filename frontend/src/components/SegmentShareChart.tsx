import type { SegmentClusterStats, SegmentStats } from '../types/push'

// 세그먼트(rank) 색 램프 — 상위는 진한 인디고, 하위로 갈수록 슬레이트(회색).
// 의도: '구매 기둥'은 진한 색이 장악, '오픈 기둥'은 회색이 장악 → 색만 봐도 대비가 읽힌다.
const RANK_COLORS = [
  '#4338ca', '#4f46e5', '#6366f1', '#818cf8', '#a5b4fc', // S1~S5
  '#94a3b8', '#a8b2c0', '#c0c8d4', '#d2d9e2', '#e2e8f0', // S6~S10
]
const UNASSIGNED_COLOR = '#eef2f7'
const colorOf = (cluster: number, rank: number | null) =>
  cluster === -1 || rank == null ? UNASSIGNED_COLOR : (RANK_COLORS[rank - 1] ?? '#cbd5e1')

const pct = (n: number, d: number) => (d > 0 ? (n / d) * 100 : 0)
const LOW_SAMPLE = 2 // 구매 표본이 이 이하면 '데이터 없음'(노이즈) 처리

type Slice = {
  key: number
  name: string
  rank: number | null
  color: string
  openShare: number
  purShare: number
  nOpen: number
  nPur: number
  lowSample: boolean
}

function Column({
  title,
  field,
  slices,
}: {
  title: string
  field: 'openShare' | 'purShare'
  slices: Slice[]
}) {
  return (
    <div className="flex-1">
      <p className="mb-1 text-center text-xs font-medium text-slate-500">{title}</p>
      <div className="flex h-72 w-full flex-col overflow-hidden rounded-lg border border-slate-200">
        {slices.map((s) => {
          const share = s[field]
          if (share <= 0) return null
          // 구매 기둥에서 표본 부족 세그먼트는 회색+빗금 느낌으로 죽인다
          const dim = field === 'purShare' && s.lowSample
          return (
            <div
              key={s.key}
              style={{ height: `${share}%`, background: dim ? '#f1f5f9' : s.color }}
              title={`${s.name} · 오픈 ${s.nOpen.toLocaleString()}(${s.openShare.toFixed(1)}%) · 구매 ${s.nPur.toLocaleString()}(${s.purShare.toFixed(1)}%)${dim ? ' · 구매 표본부족' : ''}`}
              className="flex items-center justify-center overflow-hidden text-[10px] leading-none"
            >
              {share >= 7 && (
                <span className={dim ? 'text-slate-400' : (s.rank && s.rank <= 5 ? 'font-semibold text-white' : 'text-slate-600')}>
                  {s.name} {share.toFixed(0)}%
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function SegmentShareChart({
  clusters,
  overall,
  singlePush = false,
}: {
  clusters: SegmentClusterStats[]
  overall: SegmentStats | null
  singlePush?: boolean
}) {
  const totOpen = overall?.n_open ?? 0
  const totPur = overall?.n_purchase ?? 0

  if (!overall || totOpen === 0) {
    return <p className="py-6 text-center text-sm text-slate-400">집계 데이터가 없습니다.</p>
  }

  // clusters 는 백엔드에서 이미 rank 순(미배정 마지막). 그대로 슬라이스로.
  const slices: Slice[] = clusters.map((c) => ({
    key: c.cluster,
    name: c.cluster === -1 ? '미배정' : `S${c.rank}`,
    rank: c.rank,
    color: colorOf(c.cluster, c.rank),
    openShare: pct(c.n_open, totOpen),
    purShare: pct(c.n_purchase, totPur),
    nOpen: c.n_open,
    nPur: c.n_purchase,
    lowSample: c.n_purchase <= LOW_SAMPLE,
  }))

  // 캡션: rank 순으로 누적해 구매 90% 도달 지점 → "상위 k개: 오픈 X% → 구매 Y%"
  const ranked = slices.filter((s) => s.rank != null)
  let cumOpen = 0
  let cumPur = 0
  let crossover: { k: number; open: number; pur: number } | null = null
  for (let i = 0; i < ranked.length; i++) {
    cumOpen += ranked[i].openShare
    cumPur += ranked[i].purShare
    if (cumPur >= 90 && !crossover) crossover = { k: i + 1, open: cumOpen, pur: cumPur }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <p className="mb-1 text-sm font-semibold text-slate-600">오픈 점유 vs 구매 기여</p>
      <p className="mb-3 text-xs text-slate-400">
        전체 오픈/구매를 세그먼트로 100% 분해 — 같은 색이 같은 세그먼트. 상위 세그먼트가 <b>구매</b> 기둥에서 두껍고,
        하위는 <b>오픈</b>만 차지(=구매로 안 이어지는 오픈). 구매 ≤{LOW_SAMPLE}건 세그먼트는 회색(표본부족).
      </p>
      <div className="flex items-start justify-center gap-6">
        <Column title="오픈 구성" field="openShare" slices={slices} />
        <Column title="구매 기여" field="purShare" slices={slices} />
      </div>
      {crossover && (
        <p className="mt-3 text-center text-xs text-slate-500">
          상위 <b className="text-indigo-600">{crossover.k}개 세그먼트</b>가 오픈의{' '}
          <b>{crossover.open.toFixed(0)}%</b>에서 구매의 <b className="text-indigo-600">{crossover.pur.toFixed(0)}%</b>를 만든다.
        </p>
      )}
      {singlePush && (
        <p className="mt-1 text-center text-[11px] text-amber-600">⚠ 단건 푸시 — 표본이 작아 점유 비율 노이즈가 큽니다.</p>
      )}
    </div>
  )
}
