import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { useJobPolling } from '../hooks/useJobPolling'
import {
  discoverContent,
  errorMessage,
  getMeta,
  getPushResults,
  getSegmentConversion,
  saveContentMap,
  startSegmentConversionRefresh,
} from '../services/pushService'
import type {
  DiscoverRes,
  PushResultRow,
  SegmentClusterStats,
  SegmentConversionRes,
} from '../types/push'

// 카테고리별 고유색 (서로 확실히 구분되는 7색 — 미분류는 회색 폴백)
const CAT_COLORS: Record<string, string> = {
  식품: '#16a34a', // 초록
  뷰티: '#db2777', // 핑크
  패션의류: '#2563eb', // 파랑
  패션잡화: '#9333ea', // 보라
  주얼리: '#d97706', // 앰버
  '생활/주방': '#0d9488', // 청록
  '디지털/가전': '#64748b', // 슬레이트
}

// 발송 시간대 색칩 — push_at 의 시(hour) 기준 버킷 (카테고리처럼 한눈에 구분)
const TIME_BUCKETS: { max: number; label: string; color: string }[] = [
  { max: 5, label: '새벽', color: '#3730a3' }, // 0-5
  { max: 8, label: '아침', color: '#0284c7' }, // 6-8
  { max: 11, label: '오전', color: '#0d9488' }, // 9-11
  { max: 17, label: '오후', color: '#f59e0b' }, // 12-17
  { max: 20, label: '저녁', color: '#ea580c' }, // 18-20
  { max: 23, label: '밤', color: '#7c3aed' }, // 21-23
]
function timeBucket(pushAt: string | null): { label: string; color: string } | null {
  if (!pushAt) return null
  const m = pushAt.match(/[T ](\d{1,2}):/) // "...-DD HH:MM" 또는 "...THH:MM"
  const h = m ? Number(m[1]) : NaN
  if (Number.isNaN(h)) return null
  return TIME_BUCKETS.find((b) => h <= b.max) ?? null
}

const num = (v: number | null) => (v == null ? '-' : v.toLocaleString())

export default function PushResultsPage() {
  const [rows, setRows] = useState<PushResultRow[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [pushSortDesc, setPushSortDesc] = useState(true) // 커버리지: 발송시각 정렬 방향(기본 최신순)
  const [expanded, setExpanded] = useState<Set<number>>(new Set()) // 커버리지: 펼친 push_seq (기본 접힘=S1만)
  const [rankMetric, setRankMetric] = useState<'purchase' | 'view' | 'aov' | 'n_purchase'>('view') // 성과 랭킹 정렬 기준(기본=유효시청률, 표본 안정적)
  const [rankMinOpen, setRankMinOpen] = useState(100) // 랭킹: 최소 오픈수(작은 표본 노이즈 제거)

  // 푸시 방송 매핑 · 전환 조회 (push_seq + 판매자이름 + 카테고리)
  const [fPushSeq, setFPushSeq] = useState('')
  const [fSeller, setFSeller] = useState('')
  const [fCategory, setFCategory] = useState('')
  const [categories, setCategories] = useState<string[]>([])
  const [disc, setDisc] = useState<DiscoverRes | null>(null)
  const [fBusy, setFBusy] = useState(false)
  const [fError, setFError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')

  // 세그먼트별 전환 (클러스터 × 푸시 전환율 집계)
  const [seg, setSeg] = useState<SegmentConversionRes | null>(null)
  const [segLoaded, setSegLoaded] = useState(false)
  const [segError, setSegError] = useState('')
  const [segJobId, setSegJobId] = useState<string | null>(null)
  const [segSel, setSegSel] = useState('total') // 'total' | push_seq 문자열

  useEffect(() => {
    getPushResults()
      .then(setRows)
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoading(false))
  }, [])

  // 카테고리 허용목록 로드 — 드롭다운(오타 방지). 기본값=첫 카테고리
  useEffect(() => {
    getMeta()
      .then((m) => {
        setCategories(m.categories)
        setFCategory((prev) => prev || m.categories[0] || '')
      })
      .catch(() => {})
  }, [])

  const loadSeg = useCallback(() => {
    getSegmentConversion()
      .then(setSeg)
      .catch((e) => setSegError(errorMessage(e)))
      .finally(() => setSegLoaded(true))
  }, [])

  useEffect(loadSeg, [loadSeg])

  const segJob = useJobPolling(segJobId, (j) => {
    if (j.status === 'done') loadSeg()
    setSegJobId(null)
    if (j.status === 'error') setSegError(j.error ?? '집계 실패')
  })

  const refreshSeg = async () => {
    setSegError('')
    try {
      const j = await startSegmentConversionRefresh()
      setSegJobId(j.job_id)
    } catch (e) {
      setSegError(errorMessage(e))
    }
  }

  const segRunning = segJobId !== null

  // 선택된 푸시 (없거나 'total' 이면 null)
  const segPush = useMemo(
    () => (segSel === 'total' ? null : (seg?.pushes.find((p) => String(p.push_seq) === segSel) ?? null)),
    [seg, segSel],
  )

  // 현재 표시할 클러스터 통계 + 기준 전환율(overall)
  const segClusters: SegmentClusterStats[] = segPush ? segPush.clusters : (seg?.by_cluster_total ?? [])
  const segOverall = segPush ? segPush.overall : (seg?.totals ?? null)

  const segChartData = useMemo(
    () =>
      segClusters.map((c) => ({
        name: c.cluster === -1 ? '미배정' : `S${c.rank}`,
        sname: c.short_name, // 짧은 별칭 (툴팁)
        orig: c.cluster, // 모델 원본 라벨 (추적용 — 툴팁 표기)
        view_rate: c.view_rate_pct,
        purchase_rate: c.purchase_rate_pct,
        n_open: c.n_open,
        n_view: c.n_view,
        n_purchase: c.n_purchase,
        desc: c.desc,
      })),
    [segClusters],
  )

  // 커버리지 테이블: 세그먼트 집계의 푸시들을 검색 필터 + 최신순 정렬
  const coveragePushes = useMemo(() => {
    const list = seg?.pushes ?? []
    const f = list.filter(
      (p) =>
        !q ||
        p.title?.includes(q) ||
        p.seller_name?.includes(q) ||
        p.category?.includes(q) ||
        String(p.push_seq).includes(q),
    )
    return [...f].sort((a, b) => {
      const av = a.push_at ?? ''
      const bv = b.push_at ?? ''
      const cmp = av > bv ? 1 : av < bv ? -1 : 0
      return pushSortDesc ? -cmp : cmp
    })
  }, [seg, q, pushSortDesc])

  const toggleExpand = (ps: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(ps)) next.delete(ps)
      else next.add(ps)
      return next
    })
  const allExpanded = coveragePushes.length > 0 && coveragePushes.every((p) => expanded.has(p.push_seq))
  const toggleAll = () => setExpanded(allExpanded ? new Set() : new Set(coveragePushes.map((p) => p.push_seq)))

  // 푸시 메시지 성과 랭킹 (TOP/BOTTOM 10) — seg.pushes 정렬. [집계 갱신] 시 seg 가 갱신되면 함께 최신화.
  const rankList = useMemo(() => {
    if (!seg) return null
    const keyed = seg.pushes
      .filter((p) => p.overall.n_open >= rankMinOpen)
      .map((p) => ({
        p,
        v:
          rankMetric === 'purchase'
            ? p.overall.purchase_rate_pct
            : rankMetric === 'view'
              ? p.overall.view_rate_pct
              : rankMetric === 'aov'
                ? p.overall.aov
                : p.overall.n_purchase,
      }))
    keyed.sort((a, b) => b.v - a.v)
    return {
      eligible: keyed.length,
      total: seg.pushes.length,
      top: keyed.slice(0, 10).map((k) => k.p),
      bottom: keyed.slice(Math.max(10, keyed.length - 10)).reverse().map((k) => k.p), // top 과 안 겹치게
    }
  }, [seg, rankMetric, rankMinOpen])

  const trend = useMemo(
    () =>
      [...rows]
        .filter((r) => r.push_at && r.open_rate_reach != null)
        .sort((a, b) => (a.push_at! > b.push_at! ? 1 : -1))
        .map((r) => ({
          date: r.push_at!.slice(5, 10),
          open_rate: +(r.open_rate_reach! * 100).toFixed(3),
          title: r.title,
          category: r.category,
        })),
    [rows],
  )

  const byCategory = useMemo(() => {
    const m = new Map<string, { sum: number; n: number }>()
    rows.forEach((r) => {
      if (!r.category || r.open_rate_reach == null) return
      const e = m.get(r.category) ?? { sum: 0, n: 0 }
      e.sum += r.open_rate_reach
      e.n += 1
      m.set(r.category, e)
    })
    return [...m.entries()]
      .map(([cat, { sum, n }]) => ({ category: cat, avg_open_rate: +((sum / n) * 100).toFixed(3), pushes: n }))
      .sort((a, b) => b.avg_open_rate - a.avg_open_rate)
  }, [rows])

  const runDiscover = async () => {
    const pushSeq = Number(fPushSeq)
    if (!Number.isInteger(pushSeq) || pushSeq < 1) return setFError('push_seq를 입력하세요')
    if (!fSeller.trim()) return setFError('판매자이름을 입력하세요')
    setFError('')
    setSaveMsg('')
    setDisc(null)
    setFBusy(true)
    try {
      setDisc(await discoverContent(pushSeq, fSeller.trim()))
    } catch (e) {
      setFError(errorMessage(e))
    } finally {
      setFBusy(false)
    }
  }

  // 저장 가드: 방송 1개 이상 발견 + 발송 기록 존재 (0건·미발송이면 막음)
  const canSave = !!disc && disc.contents.length > 0 && disc.n_sent > 0
  const saveMapping = async () => {
    if (!disc || !canSave) return
    setSaving(true)
    setFError('')
    try {
      await saveContentMap(
        disc.push_seq,
        disc.contents.map((c) => c.content_id),
        fCategory,
        disc.seller_name,
        disc.title,
      )
      setSaveMsg('저장됨 — 위 [집계 갱신]을 누르면 커버리지에 반영됩니다.')
    } catch (e) {
      setFError(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p className="text-slate-500">push_result 불러오는 중… (Snowflake)</p>
  if (error) return <p className="rounded-lg bg-red-50 p-4 text-red-700">{error}</p>

  return (
    <div className="flex flex-col gap-6">
      {/* 시각 순서: 세그먼트별 전환(order-1) → 푸시 전환 결과 조회(order-2) → 성과 랭킹(order-3) → 커버리지(order-4) */}
      <header className="flex items-end justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-800">푸시 현황</h2>
          <p className="text-sm text-slate-500">data_anal.push_result · {rows.length}건</p>
        </div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="제목/판매자/카테고리/push_seq 검색"
          className="w-64 rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-indigo-400 focus:outline-none"
        />
      </header>

      <div className="grid grid-cols-2 gap-4">
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-600">오픈율 추이 (도달 기준 %)</h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trend}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} unit="%" />
              <Tooltip
                formatter={(v: number) => [`${v}%`, '오픈율']}
                labelFormatter={(_, p) => (p[0]?.payload ? `${p[0].payload.title} (${p[0].payload.category})` : '')}
              />
              <Line type="monotone" dataKey="open_rate" stroke="#6366f1" dot={{ r: 3 }} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </section>
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-600">카테고리별 평균 오픈율 (%)</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={byCategory}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="category" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} unit="%" />
              <Tooltip formatter={(v: number, name) => [name === 'avg_open_rate' ? `${v}%` : v, name === 'avg_open_rate' ? '평균 오픈율' : '푸시 수']} />
              <Legend formatter={(v) => (v === 'avg_open_rate' ? '평균 오픈율(%)' : v)} />
              <Bar dataKey="avg_open_rate" fill="#818cf8" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </section>
      </div>

      <section className="order-3 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-2.5">
          <div>
            <h3 className="text-sm font-semibold text-slate-600">푸시 메시지 성과 랭킹 (TOP / BOTTOM 10)</h3>
            <p className="text-xs text-slate-400">
              등록 푸시를 메시지 단위로 줄 세웁니다 — <b>[집계 갱신]</b> 시 함께 최신화. 표본이 작으면 비율이 출렁여서 <b>최소 오픈수</b>로 거릅니다.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <select
              value={rankMetric}
              onChange={(e) => setRankMetric(e.target.value as 'purchase' | 'view' | 'aov' | 'n_purchase')}
              className="rounded-lg border border-slate-300 px-2 py-1"
            >
              <option value="purchase">거래전환율</option>
              <option value="view">유효시청률</option>
              <option value="aov">객단가</option>
              <option value="n_purchase">거래수</option>
            </select>
            <label className="flex items-center gap-1 text-slate-500">
              최소 오픈
              <input
                type="number"
                min={0}
                value={rankMinOpen}
                onChange={(e) => setRankMinOpen(Math.max(0, +e.target.value || 0))}
                className="w-16 rounded-lg border border-slate-300 px-2 py-1 text-right"
              />
            </label>
          </div>
        </div>
        {!seg ? (
          <p className="px-4 py-6 text-sm text-slate-500">집계가 없습니다 — 아래 [집계 실행]을 먼저 누르세요.</p>
        ) : !rankList || rankList.eligible === 0 ? (
          <p className="px-4 py-6 text-sm text-slate-500">최소 오픈 {rankMinOpen} 이상인 푸시가 없습니다 — 기준을 낮춰보세요.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2">
            {(
              [
                { title: '🏆 TOP 10 (성과 상위)', list: rankList.top, good: true },
                { title: '🔻 BOTTOM 10 (성과 하위)', list: rankList.bottom, good: false },
              ] as const
            ).map((col) => (
              <div key={col.title} className="border-t border-slate-100 md:border-l md:border-slate-100 md:first:border-l-0">
                <div className="bg-slate-50 px-4 py-1.5 text-xs font-semibold text-slate-500">{col.title}</div>
                <table className="w-full text-sm">
                  <tbody>
                    {col.list.map((p, idx) => {
                      const tb = timeBucket(p.push_at)
                      return (
                      <tr key={p.push_seq} className="border-t border-slate-100">
                        <td className="px-3 py-1.5 text-right text-xs tabular-nums text-slate-400">{idx + 1}</td>
                        <td className="px-2 py-1.5">
                          <div className="flex items-center gap-1.5">
                            <span className="text-[10px] tabular-nums text-slate-400">{p.push_seq}</span>
                            <span className="max-w-[12rem] truncate text-slate-700">{p.title || '-'}</span>
                          </div>
                          <span className="text-[10px] text-slate-400">
                            {tb && (
                              <span
                                className="mr-1 inline-block rounded px-1 py-px align-middle text-[9px] font-medium text-white"
                                style={{ background: tb.color }}
                              >
                                {tb.label}
                              </span>
                            )}
                            {p.category && (
                              <span
                                className="mr-1 inline-block rounded px-1 py-px align-middle text-[9px] font-medium text-white"
                                style={{ background: CAT_COLORS[p.category] ?? '#94a3b8' }}
                              >
                                {p.category}
                              </span>
                            )}
                            오픈 {num(p.overall.n_open)} · 거래 {num(p.overall.n_purchase)}
                          </span>
                        </td>
                        <td className={`px-3 py-1.5 text-right font-semibold tabular-nums ${col.good ? 'text-emerald-700' : 'text-rose-600'}`}>
                          {rankMetric === 'purchase'
                            ? `${p.overall.purchase_rate_pct}%`
                            : rankMetric === 'view'
                              ? `${p.overall.view_rate_pct}%`
                              : rankMetric === 'aov'
                                ? p.overall.aov
                                  ? `₩${p.overall.aov.toLocaleString()}`
                                  : '-'
                                : `${num(p.overall.n_purchase)}명`}
                        </td>
                      </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
        <p className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
          기준{' '}
          <b>{rankMetric === 'purchase' ? '거래전환율' : rankMetric === 'view' ? '유효시청률' : rankMetric === 'aov' ? '객단가' : '거래수'}</b>{' '}
          · 최소 오픈 {rankMinOpen} · 대상 {rankList?.eligible ?? 0}/{rankList?.total ?? 0}개 푸시. 거래는 푸시당 한 자릿수라 거래전환율 순위는 표본 영향이 큽니다 — 안정적 비교는 <b>유효시청률</b> 권장.
        </p>
      </section>

      <section className="order-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-2.5">
          <div>
            <h3 className="text-sm font-semibold text-slate-600">푸시 메세지 세그먼트별 커버리지</h3>
            <p className="text-xs text-slate-400">
              각 푸시를 세그먼트별로 분해 — <b>push 번호 클릭 시 S1 아래로 펼침</b>. 발송·도달·오픈·유효시청·거래{' '}
              <b>인원수</b> + 율(작은 글씨, 오픈자 기준). 괄호는 위쪽 세그먼트까지 <b>누적 커버리지</b>(발송·도달·오픈·거래 모두 마지막 행 100%) — 거래는 상위 세그먼트가 전체 구매자의 몇 %를 커버하는지. 발송/도달은 [집계 갱신] 시점 기준.{' '}
              <span className="font-semibold text-indigo-600">굵은 인디고 선</span> = 위쪽 세그먼트로 오픈자 누적 50% 도달 지점.
            </p>
          </div>
          {seg && (
            <button
              onClick={toggleAll}
              className="shrink-0 whitespace-nowrap rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
            >
              {allExpanded ? '모두 접기' : '모두 펼치기'}
            </button>
          )}
        </div>
        {!seg ? (
          <p className="px-4 py-6 text-sm text-slate-500">
            세그먼트 집계가 없습니다 — 위 <b>세그먼트별 전환율</b>에서 <b>[집계 실행]</b>을 먼저 누르세요.
          </p>
        ) : (
          <div className="max-h-[26rem] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-50 text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left font-medium">push</th>
                <th className="px-3 py-2 text-left font-medium">판매자</th>
                <th className="px-3 py-2 text-left font-medium">제목</th>
                <th className="px-3 py-2 text-left font-medium">카테고리</th>
                <th
                  className="cursor-pointer whitespace-nowrap px-3 py-2 text-left font-medium hover:text-indigo-600"
                  onClick={() => setPushSortDesc((d) => !d)}
                  title="클릭 시 발송시각 정렬 토글"
                >
                  발송시각 {pushSortDesc ? '↓' : '↑'}
                </th>
                <th className="px-3 py-2 text-right font-medium">전체발송</th>
                <th className="px-3 py-2 text-right font-medium">전체도달</th>
                <th className="px-3 py-2 text-right font-medium">전체오픈</th>
                <th className="px-3 py-2 text-right font-medium">전체유효시청</th>
                <th className="px-3 py-2 text-right font-medium">전체거래</th>
                <th className="border-l border-slate-200 px-3 py-2 text-left font-medium">세그먼트</th>
                <th className="px-3 py-2 text-right font-medium">발송</th>
                <th className="px-3 py-2 text-right font-medium">도달</th>
                <th className="px-3 py-2 text-right font-medium">오픈</th>
                <th className="px-3 py-2 text-right font-medium">유효시청</th>
                <th className="px-3 py-2 text-right font-medium">거래전환</th>
              </tr>
            </thead>
            <tbody>
              {coveragePushes.map((p) => {
                const isExpanded = expanded.has(p.push_seq)
                const visibleSegs = isExpanded ? p.clusters : p.clusters.slice(0, 1) // 접힘=S1만
                return visibleSegs.map((c, i) => {
                  // 그 푸시 안에서 S1→미배정 순 누적비율 (분모 = 푸시 전체, 마지막 행=100%)
                  // n_purchase = 거래(구매자) 커버리지 — 구매자 총수가 고정 분모라 마지막 행에서 100% 수렴
                  const cum = (key: 'n_sent' | 'n_reached' | 'n_open' | 'n_purchase') => {
                    const denom = p.overall[key]
                    if (!denom) return ''
                    const total = p.clusters.slice(0, i + 1).reduce((acc, x) => acc + x[key], 0)
                    return ` (${((total / denom) * 100).toFixed(1)}%)`
                  }
                  // 오픈 누적이 50%를 처음 넘는 경계 행 (위쪽 세그먼트들이 오픈자 절반을 커버) → 굵은 선
                  const denOpen = p.overall.n_open
                  const cumOpen = p.clusters.slice(0, i + 1).reduce((acc, x) => acc + x.n_open, 0)
                  const open50 = denOpen > 0 && cumOpen / denOpen >= 0.5 && (cumOpen - c.n_open) / denOpen < 0.5
                  const segBorder = open50 ? ' border-b-2 border-indigo-500' : ''
                  return (
                  <tr
                    key={`${p.push_seq}-${c.cluster}`}
                    className={`${i === 0 ? 'border-t-2 border-slate-200' : 'border-t border-slate-100'} ${
                      c.n_sent === 0 && c.n_open === 0 ? 'text-slate-300' : ''
                    }`}
                  >
                    {i === 0 && (
                      <>
                        <td
                          rowSpan={visibleSegs.length}
                          className="cursor-pointer whitespace-nowrap px-3 py-2 align-top text-slate-500 hover:text-indigo-600"
                          title={isExpanded ? '접기' : '세그먼트 펼치기'}
                          onClick={() => toggleExpand(p.push_seq)}
                        >
                          <span className="mr-1 inline-block w-3 text-slate-400">{isExpanded ? '▾' : '▸'}</span>
                          {p.push_seq}
                        </td>
                        <td
                          rowSpan={visibleSegs.length}
                          className="whitespace-nowrap px-3 py-2 align-top font-medium text-slate-600"
                        >
                          {p.seller_name || '-'}
                        </td>
                        <td rowSpan={visibleSegs.length} className="px-3 py-2 align-top">
                          <div className="max-w-[14rem] truncate text-slate-700">{p.title || '-'}</div>
                        </td>
                        <td rowSpan={visibleSegs.length} className="px-3 py-2 align-top">
                          {p.category ? (
                            <span
                              className="whitespace-nowrap rounded-full px-2 py-0.5 text-xs text-white"
                              style={{ background: CAT_COLORS[p.category] ?? '#94a3b8' }}
                            >
                              {p.category}
                            </span>
                          ) : (
                            <span className="text-slate-400">-</span>
                          )}
                        </td>
                        <td rowSpan={visibleSegs.length} className="whitespace-nowrap px-3 py-2 align-top text-xs text-slate-500">
                          {p.push_at ? p.push_at.slice(5, 16) : '-'}
                        </td>
                        <td rowSpan={visibleSegs.length} className="px-3 py-2 text-right align-top tabular-nums text-slate-600">
                          {p.overall.n_sent.toLocaleString()}
                        </td>
                        <td rowSpan={visibleSegs.length} className="px-3 py-2 text-right align-top tabular-nums text-slate-600">
                          {p.overall.n_reached.toLocaleString()}
                        </td>
                        <td rowSpan={visibleSegs.length} className="px-3 py-2 text-right align-top tabular-nums text-slate-600">
                          {p.overall.n_open.toLocaleString()}
                        </td>
                        <td rowSpan={visibleSegs.length} className="px-3 py-2 text-right align-top tabular-nums text-slate-600">
                          {p.overall.n_view.toLocaleString()}
                        </td>
                        <td rowSpan={visibleSegs.length} className="px-3 py-2 text-right align-top tabular-nums font-semibold text-emerald-700">
                          {p.overall.n_purchase.toLocaleString()}
                        </td>
                      </>
                    )}
                    <td className={`whitespace-nowrap border-l border-slate-200 px-3 py-1 text-xs${segBorder}`}>
                      {c.cluster === -1 ? '미배정' : `S${c.rank} ${c.short_name}`}
                      {!isExpanded && p.clusters.length > 1 && (
                        <span className="ml-1 text-[10px] text-slate-400">외 {p.clusters.length - 1}개 ▸</span>
                      )}
                    </td>
                    <td className={`px-3 py-1 text-right text-xs tabular-nums${segBorder}`}>
                      {num(c.n_sent)}
                      <span className="ml-1 text-[10px] text-slate-400">{cum('n_sent')}</span>
                    </td>
                    <td className={`px-3 py-1 text-right text-xs tabular-nums${segBorder}`}>
                      {num(c.n_reached)}
                      <span className="ml-1 text-[10px] text-slate-400">{cum('n_reached')}</span>
                    </td>
                    <td className={`px-3 py-1 text-right text-xs tabular-nums${segBorder}`}>
                      {num(c.n_open)}
                      <span className={`ml-1 text-[10px] ${open50 ? 'font-bold text-indigo-600' : 'text-slate-400'}`}>
                        {cum('n_open')}
                      </span>
                    </td>
                    <td className={`px-3 py-1 text-right text-xs tabular-nums${segBorder}`}>
                      {num(c.n_view)}
                      <span className="ml-1 text-[10px] text-slate-400">({c.view_rate_pct}%)</span>
                    </td>
                    <td className={`px-3 py-1 text-right text-xs tabular-nums${segBorder}`}>
                      <span className="font-semibold">{num(c.n_purchase)}</span>
                      <span className="ml-1 text-[10px] text-slate-400">({c.purchase_rate_pct}%){cum('n_purchase')}</span>
                    </td>
                  </tr>
                  )
                })
              })}
            </tbody>
          </table>
          </div>
        )}
      </section>

      {/* 최근 방송 오픈 vs 비오픈 (발송 모수 내) — 시청·구매율 */}
      <section className="order-5 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-4 py-2.5">
          <h3 className="text-sm font-semibold text-slate-600">
            최근 {seg?.open_compare_n_pushes ?? 10}개 방송 종합 — 세그먼트별 오픈자 vs 비오픈자
          </h3>
          <p className="text-xs leading-relaxed text-slate-400">
            발송받은 사람을 세그먼트(활성도)별로 묶어, 각 안에서 <b>오픈 / 비오픈</b>의 유효시청·구매율을 비교. <b>[집계 갱신]</b> 시 최신화.
            <br />※ 같은 세그먼트 안에서도 오픈자 율이 높지만, 이는 푸시 효과가 아니라 <b>관심 있는 사람이 오픈도 한다</b>는 자기선택일 수 있어요 — 진짜 효과는 A/B로. (하위 세그먼트는 오픈 수가 적어 율이 출렁입니다.)
          </p>
        </div>
        {!seg || !seg.open_compare || seg.open_compare.length === 0 ? (
          <p className="px-4 py-6 text-sm text-slate-500">집계 데이터가 없습니다 — <b>세그먼트별 전환율</b>에서 [집계 실행/갱신]을 누르세요.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left font-medium">세그먼트</th>
                <th className="px-3 py-2 text-right font-medium">발송</th>
                <th className="px-3 py-2 text-right font-medium">오픈(율)</th>
                <th className="border-l border-slate-200 px-3 py-2 text-right font-medium">오픈자 시청율</th>
                <th className="px-3 py-2 text-right font-medium">오픈자 구매율</th>
                <th className="border-l border-slate-200 px-3 py-2 text-right font-medium">비오픈 시청율</th>
                <th className="px-3 py-2 text-right font-medium">비오픈 구매율</th>
              </tr>
            </thead>
            <tbody>
              {seg.open_compare.map((r) => {
                const sparse = r.n_open < 30 // 오픈 표본 적으면 율 신뢰도 낮음 → 흐리게
                return (
                  <tr key={r.cluster} className={`border-t border-slate-100 ${sparse ? 'opacity-50' : ''}`}>
                    <td className="px-3 py-2">
                      {r.cluster === -1 ? (
                        <span className="font-semibold text-slate-500">미배정</span>
                      ) : (
                        <span className="font-semibold text-slate-700">
                          S{r.rank} {r.short_name}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">{num(r.n_sent)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                      {num(r.n_open)}
                      <span className="ml-1 text-[10px] text-slate-400">({r.open_rate_pct}%)</span>
                    </td>
                    <td className="border-l border-slate-200 px-3 py-2 text-right font-semibold tabular-nums text-indigo-600">
                      {r.opener_view_rate_pct}%
                    </td>
                    <td className="px-3 py-2 text-right font-semibold tabular-nums text-indigo-600">
                      {r.opener_purchase_rate_pct}%
                    </td>
                    <td className="border-l border-slate-200 px-3 py-2 text-right tabular-nums text-slate-400">
                      {r.nonopener_view_rate_pct}%
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-400">{r.nonopener_purchase_rate_pct}%</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      <section className="order-2 rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-slate-600">푸시 방송 매칭 및 결과 데이터 추가</h3>
          <p className="text-xs leading-relaxed text-slate-400">
            <b>push_seq · 판매자이름 · 카테고리</b> 3개만 입력하면 발송시각 전후 방송을 자동으로 찾아 오픈→유효시청→구매를 미리 보여주고,{' '}
            <b>[커버리지에 추가]</b> 시 위 '세그먼트별 전환율·커버리지'에 들어갑니다(이후 <b>[집계 갱신]</b> 필요).
            <br />
            <span className="text-amber-600">⚠ 판매자이름은 띄어쓰기·대소문자·이모지까지 정확히 일치해야 합니다</span>{' '}
            (예: <code className="rounded bg-slate-100 px-1">SABABA 사바바</code>).
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm text-slate-500">
            push_seq{' '}
            <input
              type="number"
              value={fPushSeq}
              min={1}
              onChange={(e) => setFPushSeq(e.target.value)}
              className="w-24 rounded-lg border border-slate-300 px-2 py-1 text-right text-sm"
            />
          </label>
          <label className="text-sm text-slate-500">
            판매자이름{' '}
            <input
              value={fSeller}
              onChange={(e) => setFSeller(e.target.value)}
              placeholder="정확히 일치"
              className="w-44 rounded-lg border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="text-sm text-slate-500">
            카테고리{' '}
            <select
              value={fCategory}
              onChange={(e) => setFCategory(e.target.value)}
              className="rounded-lg border border-slate-300 px-2 py-1 text-sm"
            >
              {categories.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <button
            onClick={runDiscover}
            disabled={fBusy}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-indigo-700 disabled:opacity-40"
          >
            {fBusy ? '조회 중…' : '조회'}
          </button>
        </div>

        {fError && <p className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{fError}</p>}

        {disc && (
          <div className="mt-4 space-y-4">
            <div className="text-xs text-slate-500">
              push <b className="text-slate-700">{disc.push_seq}</b>
              {disc.title ? ` · ${disc.title}` : ''}
              {disc.push_at ? ` · 발송 ${disc.push_at.slice(0, 16)}` : ''} · 발송 {disc.n_sent.toLocaleString()}명
              {disc.seller_matches > 1 ? ` · ⚠ 판매자 ${disc.seller_matches}명 일치` : ''}
            </div>

            {disc.warnings.length > 0 && (
              <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                {disc.warnings.map((w, i) => (
                  <p key={i}>⚠ {w}</p>
                ))}
              </div>
            )}

            {disc.contents.length > 0 && (
              <div className="overflow-hidden rounded-xl border border-slate-200">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-slate-500">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">발견된 방송 ({disc.contents.length})</th>
                      <th className="px-3 py-2 text-left font-medium">content_id</th>
                      <th className="px-3 py-2 text-left font-medium">방송시각</th>
                      <th className="px-3 py-2 text-right font-medium">조회수</th>
                    </tr>
                  </thead>
                  <tbody>
                    {disc.contents.map((c) => (
                      <tr key={c.content_seq} className="border-t border-slate-100">
                        <td className="max-w-xs truncate px-3 py-1.5">{c.title || '-'}</td>
                        <td className="px-3 py-1.5 text-slate-400">{c.content_id}</td>
                        <td className="px-3 py-1.5 text-slate-500">{c.created_at?.slice(5, 16)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{c.view_count.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {disc.funnel && (
              <div className="flex items-center gap-3">
                {[
                  { label: '오픈 유저', n: disc.funnel.n_open, sub: null as number | null },
                  { label: '유효시청', n: disc.funnel.n_valid_watch, sub: disc.funnel.n_open },
                  { label: '구매', n: disc.funnel.n_purchase, sub: disc.funnel.n_open },
                ].map((s, i) => (
                  <div key={s.label} className="flex items-center gap-3">
                    {i > 0 && <span className="text-lg text-slate-300">→</span>}
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-5 py-3 text-center">
                      <p className="text-xs text-slate-500">{s.label}</p>
                      <p className="text-xl font-bold text-slate-800">{s.n.toLocaleString()}명</p>
                      {s.sub != null && (
                        <p className="text-xs font-semibold text-indigo-600">
                          오픈 대비 {s.sub > 0 ? ((s.n / s.sub) * 100).toFixed(1) : '-'}%
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <button
                onClick={saveMapping}
                disabled={!canSave || saving}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-emerald-700 disabled:opacity-40"
              >
                {saving ? '저장 중…' : `커버리지에 추가 (${fCategory})`}
              </button>
              {!canSave && (
                <span className="text-xs text-amber-600">방송이 1개 이상 발견되고 발송 기록이 있어야 추가됩니다.</span>
              )}
              {saveMsg && <span className="text-xs font-semibold text-emerald-600">✓ {saveMsg}</span>}
            </div>
          </div>
        )}
      </section>

      <section className="order-1 rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3 flex items-end justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-600">
              세그먼트별 전환율{seg ? ` (${seg.period_start.slice(5)}~${seg.period_end.slice(5)} 방송)` : ''}
            </h3>
            <p className="text-xs text-slate-400">
              수기 확정한 push→방송 매핑 기준 — 푸시를 오픈한 뒤(<b>발송시각 이후</b>) 해당 방송을 유효시청·구매한 유저를 세그먼트별로 집계합니다
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <button
              onClick={refreshSeg}
              disabled={segRunning}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-indigo-700 disabled:opacity-40"
            >
              {segRunning ? '집계 중…' : seg ? '집계 갱신' : '집계 실행'}
            </button>
            <p className="text-xs text-slate-400">현재 스냅샷 기준 · 수 분 소요</p>
          </div>
        </div>

        {segError && <p className="mb-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{segError}</p>}

        {segRunning && (
          <div className="mb-3 flex items-center gap-3 rounded-xl border border-indigo-200 bg-indigo-50 p-4">
            <span className="h-3 w-3 animate-ping rounded-full bg-indigo-500" />
            <div className="text-sm text-indigo-800">
              <b>{segJob?.stage ?? '시작 중…'}</b>
              <span className="ml-2 text-indigo-500">수 분 소요 — 페이지를 떠나도 작업은 계속됩니다</span>
            </div>
          </div>
        )}

        {segLoaded && !seg && !segRunning && (
          <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-10 text-center text-slate-500">
            아직 집계가 없습니다. <b>[집계 실행]</b>을 눌러 세그먼트별 전환율을 계산하세요.
          </div>
        )}

        {seg && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-indigo-800">
                기간 <b>{seg.period_start} ~ {seg.period_end}</b>
              </span>
              <span className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-indigo-800">
                푸시 <b>{seg.n_pushes}</b>개
              </span>
              <span className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-slate-600">
                오픈 <b>{seg.totals.n_open.toLocaleString()}</b>명
              </span>
              <span className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-slate-600">
                시청전환 <b className="text-indigo-600">{seg.totals.view_rate_pct}%</b>
              </span>
              <span className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-slate-600">
                구매전환 <b className="text-indigo-600">{seg.totals.purchase_rate_pct}%</b>
              </span>
              <span className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-slate-600">
                종합 객단가 <b className="text-emerald-700">{seg.totals.aov ? `₩${seg.totals.aov.toLocaleString()}` : '-'}</b>
              </span>
              <span className="text-xs text-slate-400">
                집계 {seg.computed_at.replace('T', ' ').slice(0, 16)} · 스냅샷 기준 {seg.cluster_snapshot_at.slice(0, 16)}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <label className="text-sm text-slate-500">푸시 선택</label>
              <select
                value={segSel}
                onChange={(e) => setSegSel(e.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-indigo-400 focus:outline-none"
              >
                <option value="total">전체 푸시 종합</option>
                {seg.pushes.map((p) => (
                  <option key={p.push_seq} value={String(p.push_seq)}>
                    [{p.push_seq}] {p.title}{p.push_at ? ` (${p.push_at.slice(5, 10).replace('-', '/')})` : ''}
                  </option>
                ))}
              </select>
            </div>

            {segPush && (
              <p className="text-xs text-slate-400">
                방송 {segPush.content_ids.join(', ') || '—'} (수기 확정 매핑
                {segPush.content_ids.length > 1 ? ` · ${segPush.content_ids.length}개 방송` : ''})
              </p>
            )}

            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={segChartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} unit="%" />
                  <Tooltip
                    formatter={(v, name): [string, string] => {
                      const label: string = name === 'view_rate' ? '시청전환율' : '구매전환율'
                      return [`${v}%`, label]
                    }}
                    labelFormatter={(label, p) => {
                      const d = p[0]?.payload as (typeof segChartData)[number] | undefined
                      if (!d) return label
                      const tag = d.orig === -1 ? d.name : `${d.name} ${d.sname} (원본 C${d.orig})`
                      return `${tag} · ${d.desc} (오픈 ${d.n_open.toLocaleString()}명 · 시청 ${d.n_view.toLocaleString()} · 구매 ${d.n_purchase.toLocaleString()})`
                    }}
                  />
                  <Legend formatter={(v) => (v === 'view_rate' ? '시청전환율(%)' : '구매전환율(%)')} />
                  {segOverall && (
                    <ReferenceLine
                      y={segOverall.view_rate_pct}
                      stroke="#6366f1"
                      strokeDasharray="4 4"
                      label={{ value: `전체 시청 ${segOverall.view_rate_pct}%`, fontSize: 10, fill: '#6366f1', position: 'insideTopLeft' }}
                    />
                  )}
                  {segOverall && (
                    <ReferenceLine
                      y={segOverall.purchase_rate_pct}
                      stroke="#db2777"
                      strokeDasharray="4 4"
                      label={{ value: `전체 구매 ${segOverall.purchase_rate_pct}%`, fontSize: 10, fill: '#db2777', position: 'insideBottomLeft' }}
                    />
                  )}
                  <Bar dataKey="view_rate" fill="#818cf8" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="purchase_rate" fill="#f472b6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">세그먼트</th>
                  <th className="px-3 py-2 text-left font-medium">설명</th>
                  <th className="px-3 py-2 text-right font-medium">오픈</th>
                  <th className="px-3 py-2 text-right font-medium">유효시청 (n · %)</th>
                  <th className="px-3 py-2 text-right font-medium">구매 (n · %)</th>
                  <th className="px-3 py-2 text-right font-medium" title="전환 구매 GMV 합 ÷ 구매자 수 (구매자 1인당)">객단가</th>
                </tr>
              </thead>
              <tbody>
                {segClusters.map((c) => {
                  const empty = c.n_open === 0
                  return (
                    <tr
                      key={c.cluster}
                      className={`border-t border-slate-100 ${empty ? 'text-slate-300' : ''}`}
                    >
                      <td className="px-3 py-2">
                        {c.cluster === -1 ? (
                          <span className="font-semibold">미배정</span>
                        ) : (
                          <>
                            <span className="font-semibold">S{c.rank} {c.short_name}</span>
                            <span className="ml-1 text-xs text-slate-400">원본 C{c.cluster}</span>
                          </>
                        )}
                      </td>
                      <td className="max-w-xs truncate px-3 py-2">{c.desc}</td>
                      <td className="px-3 py-2 text-right">{c.n_open.toLocaleString()}</td>
                      <td className="px-3 py-2 text-right">
                        {c.n_view.toLocaleString()} · <span className="font-semibold">{c.view_rate_pct}%</span>
                      </td>
                      <td className="px-3 py-2 text-right">
                        {c.n_purchase.toLocaleString()} · <span className="font-semibold">{c.purchase_rate_pct}%</span>
                      </td>
                      <td
                        className="px-3 py-2 text-right tabular-nums"
                        title={c.gmv_sum ? `전환 GMV 합 ₩${c.gmv_sum.toLocaleString()}` : undefined}
                      >
                        {c.aov ? (
                          <span className="font-semibold text-emerald-700">₩{c.aov.toLocaleString()}</span>
                        ) : (
                          <span className="text-slate-300">-</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
