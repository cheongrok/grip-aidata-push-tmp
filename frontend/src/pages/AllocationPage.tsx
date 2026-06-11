import { useEffect, useState } from 'react'
import AbTestSection from '../components/AbTestSection'
import { createAllocation, errorMessage, getMeta, getSnapshot } from '../services/pushService'
import type { AllocationRes, ClusterCard, MetaRes, PushInput } from '../types/push'

const HOUR_LABEL: Record<string, string> = {
  '1_morning(6-11)': '오전 (6-11시)',
  '2_afternoon(12-17)': '오후 (12-17시)',
  '3_evening(18-23)': '저녁 (18-23시)',
}

const TIER_DOT: Record<string, string> = { Tier1: '🟢', Tier2: '🟡', Tier3: '🔴' }

export default function AllocationPage() {
  const [meta, setMeta] = useState<MetaRes | null>(null)
  const [clusters, setClusters] = useState<ClusterCard[]>([]) // 수동 선택용 (스냅샷)
  const [mode, setMode] = useState<'auto' | 'manual'>('auto')
  const [pushes, setPushes] = useState<PushInput[]>([{ title: '', category: '식품', hour_bucket: null, clusters: [] }])
  const [topK, setTopK] = useState(5)
  const [holdoutPct, setHoldoutPct] = useState(0) // A/B 대조군 % (0~50)
  const [result, setResult] = useState<AllocationRes | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getMeta().then(setMeta).catch((e) => setError(errorMessage(e)))
    getSnapshot()
      .then((s) => setClusters(s?.clusters ?? []))
      .catch(() => {})
  }, [])

  const update = (i: number, patch: Partial<PushInput>) =>
    setPushes((ps) => ps.map((p, j) => (j === i ? { ...p, ...patch } : p)))

  const toggleCluster = (i: number, cid: number) =>
    setPushes((ps) =>
      ps.map((p, j) => {
        if (j !== i) return p
        const cur = p.clusters ?? []
        return { ...p, clusters: cur.includes(cid) ? cur.filter((c) => c !== cid) : [...cur, cid] }
      }),
    )

  const manualIncomplete = mode === 'manual' && pushes.some((p) => !(p.clusters && p.clusters.length))

  const run = async () => {
    setError('')
    if (manualIncomplete) {
      setError('수동 모드에서는 모든 푸시에 그룹을 1개 이상 선택하세요.')
      return
    }
    setBusy(true)
    setResult(null)
    try {
      const payload: PushInput[] = pushes.map((p) =>
        mode === 'manual'
          ? { title: p.title, category: p.category, hour_bucket: p.hour_bucket, clusters: p.clusters }
          : { title: p.title, category: p.category, hour_bucket: p.hour_bucket },
      )
      setResult(await createAllocation(payload, topK, 42, holdoutPct / 100))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const snapshotMissing = meta != null && !meta.snapshot.exists
  const hasHoldout = result != null && result.per_push.some((p) => p.holdout_rows > 0)
  // 스냅샷 기준일/경과일 — 분배 실행 전 최신화 점검(소프트). snapshot_at = "YYYY-MM-DD HH:MM:SS"
  const snapAt = meta?.snapshot.snapshot_at ?? null
  const snapAgeDays =
    snapAt != null ? Math.floor((Date.now() - new Date(snapAt.replace(' ', 'T')).getTime()) / 86_400_000) : null

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-bold text-slate-800">푸시 분배</h2>
        <p className="text-sm text-slate-500">
          카테고리·시간대를 입력하면 상위 세그먼트부터 비교우위로 매칭해 푸시별 발송 대상 user_id CSV를 만들어요
        </p>
      </header>

      {snapshotMissing && (
        <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
          ⚠ 유저 세그먼트 스냅샷이 없어요 — <b>유저 세그먼트</b> 페이지에서 먼저 최신화하세요.
        </p>
      )}
      {meta?.snapshot.stale && (
        <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
          ⚠ 스냅샷이 7일 이상 지났어요 ({meta.snapshot.snapshot_at?.slice(0, 16)}) — 발송 전 최신화를 권장해요.
        </p>
      )}

      <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
        {/* 발송 대상 모드 */}
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-500">발송 대상:</span>
          <div className="inline-flex rounded-lg border border-slate-300 p-0.5">
            <button
              onClick={() => setMode('auto')}
              className={`rounded px-3 py-1 ${mode === 'auto' ? 'bg-indigo-600 text-white' : 'text-slate-600'}`}
            >
              자동 (상위 N개)
            </button>
            <button
              onClick={() => setMode('manual')}
              className={`rounded px-3 py-1 ${mode === 'manual' ? 'bg-indigo-600 text-white' : 'text-slate-600'}`}
            >
              수동 (그룹 직접 선택)
            </button>
          </div>
          {mode === 'manual' && (
            <span className="text-xs text-slate-400">각 푸시가 갈 그룹을 칩으로 직접 고르세요 (겹치면 그 그룹은 나눠 보냄)</span>
          )}
        </div>

        {pushes.map((p, i) => (
          <div key={i} className="space-y-2 rounded-lg border border-slate-100 bg-slate-50/50 p-2">
            <div className="flex items-center gap-2">
              <span className="w-14 text-sm font-semibold text-slate-400">푸시 {i + 1}</span>
              <input
                value={p.title ?? ''}
                onChange={(e) => update(i, { title: e.target.value })}
                placeholder="제목 (메모용)"
                className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
              />
              <select
                value={p.category}
                onChange={(e) => update(i, { category: e.target.value })}
                className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
              >
                {meta?.categories.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
              <select
                value={p.hour_bucket ?? ''}
                onChange={(e) => update(i, { hour_bucket: e.target.value || null })}
                className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
              >
                <option value="">시간대 무관</option>
                {meta?.hour_buckets.map((h) => (
                  <option key={h} value={h}>
                    {HOUR_LABEL[h] ?? h}
                  </option>
                ))}
              </select>
              <button
                onClick={() => setPushes((ps) => ps.filter((_, j) => j !== i))}
                disabled={pushes.length === 1}
                className="rounded-lg px-2 py-1 text-slate-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-30"
              >
                ✕
              </button>
            </div>

            {mode === 'manual' && (
              <div className="flex flex-wrap items-center gap-1.5 pl-16">
                {clusters.length === 0 && <span className="text-xs text-slate-400">스냅샷이 없어 그룹을 불러올 수 없어요.</span>}
                {clusters.map((c) => {
                  const on = (p.clusters ?? []).includes(c.cluster)
                  return (
                    <button
                      key={c.cluster}
                      onClick={() => toggleCluster(i, c.cluster)}
                      title={c.short_desc}
                      className={`rounded-full border px-2 py-0.5 text-xs ${
                        on
                          ? 'border-indigo-500 bg-indigo-50 font-semibold text-indigo-700'
                          : 'border-slate-200 text-slate-500 hover:border-slate-300'
                      }`}
                    >
                      {TIER_DOT[c.tier] ?? ''} S{c.rank} {c.short_name}{' '}
                      <span className="text-slate-400">({c.count.toLocaleString()})</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        ))}

        {/* 분배 실행 전 스냅샷 최신화 점검 (소프트) — 결정 지점에 기준일·경과일 노출 */}
        {meta?.snapshot.exists && snapAgeDays != null && (
          <div
            className={`rounded-lg border px-3 py-2 text-xs ${
              snapAgeDays >= 1
                ? 'border-red-200 bg-red-50 text-red-700'
                : 'border-slate-200 bg-slate-50 text-slate-500'
            }`}
          >
            {snapAgeDays >= 1 ? '⚠ ' : '📌 '}
            유저 세그먼트 스냅샷 기준: <b>{snapAt?.slice(0, 10)}</b> ({snapAgeDays === 0 ? '오늘' : `${snapAgeDays}일 전`})
            {snapAgeDays >= 1 &&
              ' — 그 이후 새로 시청을 시작한 유저는 이 명단에 없어요. 발송이라면 [유저 세그먼트] 탭에서 최신화했는지 확인하세요.'}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
          <button
            onClick={() =>
              setPushes((ps) => [...ps, { title: '', category: '식품', hour_bucket: null, clusters: [] }])
            }
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            + 푸시 추가
          </button>
          <div className="flex items-center gap-4">
            {mode === 'auto' && (
              <label className="flex items-center gap-2 text-sm text-slate-600">
                발송 풀: 상위
                <input
                  type="range"
                  min={1}
                  max={10}
                  value={topK}
                  onChange={(e) => setTopK(+e.target.value)}
                  className="w-28"
                />
                <b className="w-20">{topK}개 세그먼트</b>
              </label>
            )}
            <label className="flex items-center gap-1.5 text-sm text-slate-600" title="발송 대상에서 무작위로 빼두는 대조군 비율 (방송 후 구매율 비교로 순효과 측정)">
              A/B 대조군
              <input
                type="number"
                min={0}
                max={50}
                value={holdoutPct}
                onChange={(e) => setHoldoutPct(Math.max(0, Math.min(50, +e.target.value || 0)))}
                className="w-16 rounded-lg border border-slate-300 px-2 py-1 text-right text-sm"
              />
              %
            </label>
            <button
              onClick={run}
              disabled={busy || snapshotMissing || manualIncomplete}
              className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white shadow hover:bg-indigo-700 disabled:opacity-40"
            >
              {busy ? '계산 중…' : '분배 실행'}
            </button>
          </div>
        </div>
        <p className="text-xs leading-relaxed text-slate-400">
          💡 반응이 좋은 <b className="text-slate-600">상위 그룹만 골라 보내면</b> 발송량은 확 줄고 클릭은 대부분 그대로예요.
          예를 들어 상위 5개(🟢 상시 발송 · 🟡 조건부 발송)만 보내면, 전체의 41%만 발송해도 클릭의 2/3 정도가 유지된 걸 6월에 확인했어요.{' '}
          화면의 예상 수치는 5월 데이터로 계산한 참고용 값이라, 실제 효과는 일부를 무작위로 나눠 비교(A/B)해 보면 가장 정확해요.
        </p>
      </section>

      {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {result && (
        <>
          {result.delivery_rate != null && result.delivery_rate < 1 && (
            <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
              기대 오픈·오픈율은 <b className="text-slate-700">발송 대비</b> 기준이에요 — 모델 CTR(도달 대비)에 도달률{' '}
              <b className="text-slate-700">{(result.delivery_rate * 100).toFixed(1)}%</b>를 반영했어요 ({result.delivery_basis}).
              <br />※ 도달률은 카테고리 간엔 균일하나 세그먼트별 편차는 측정 불가(유저단위 발송 로그 없음)라 글로벌값을 일괄 적용 — 실효과는 무작위 A/B로 확인하세요.
            </p>
          )}
          {hasHoldout && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              🧪 A/B 모드: 각 푸시 발송 대상에서 대조군(미발송)을 무작위로 분리했어요. <b>발송 CSV만 실제 발송</b>하고,
              방송 후 발송군 vs 대조군의 그 방송 상품 구매율 차이로 순효과(incremental)를 측정하세요.
            </p>
          )}
          <section className="grid gap-4" style={{ gridTemplateColumns: `repeat(${Math.min(result.per_push.length, 3)}, 1fr)` }}>
            {result.per_push.map((pp, j) => (
              <article key={j} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="mb-3 flex items-start justify-between gap-2">
                  <h3 className="font-semibold text-slate-700">
                    {pp.title || `푸시 ${j + 1}`}
                    <span className="ml-2 text-xs font-normal text-slate-400">
                      {pp.category}
                      {pp.hour_bucket ? ` · ${HOUR_LABEL[pp.hour_bucket] ?? pp.hour_bucket}` : ''}
                    </span>
                  </h3>
                  {pp.control_download_url ? (
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <a
                        href={pp.download_url}
                        className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700"
                      >
                        발송 CSV ({pp.csv_rows.toLocaleString()}명)
                      </a>
                      <a
                        href={pp.control_download_url}
                        className="rounded-lg border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        대조군 CSV ({pp.holdout_rows.toLocaleString()}명)
                      </a>
                    </div>
                  ) : (
                    <a
                      href={pp.download_url}
                      className="shrink-0 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700"
                    >
                      CSV 다운로드 ({pp.csv_rows.toLocaleString()}명)
                    </a>
                  )}
                </div>
                <dl className="grid grid-cols-2 gap-y-1.5 text-sm">
                  <dt className="text-slate-500">타겟 발송</dt>
                  <dd className="text-right font-semibold">
                    {pp.target_sends.toLocaleString()}명
                    {pp.holdout_rows > 0 && (
                      <span className="ml-1 text-xs font-normal text-slate-400">
                        (발송 {pp.csv_rows.toLocaleString()}·대조 {pp.holdout_rows.toLocaleString()})
                      </span>
                    )}
                  </dd>
                  <dt className="text-slate-500">기대 오픈</dt>
                  <dd className="text-right font-semibold">
                    {pp.expected_opens.toLocaleString()}건 ({pp.expected_open_rate_pct.toFixed(2)}%)
                  </dd>
                  <dt className="text-slate-500">전체발송 시</dt>
                  <dd className="text-right text-slate-500">
                    {pp.full_expected_opens.toLocaleString()}건 ({pp.full_open_rate_pct.toFixed(2)}%)
                  </dd>
                  <dt className="border-t border-slate-100 pt-1.5 text-slate-500">클릭 커버리지</dt>
                  <dd className="border-t border-slate-100 pt-1.5 text-right font-bold text-indigo-600">
                    {pp.click_coverage_pct != null ? `${pp.click_coverage_pct}%` : '—'}
                  </dd>
                  <dt className="text-slate-500">발송량 절감</dt>
                  <dd className="text-right font-bold text-emerald-600">-{pp.send_reduction_pct}%</dd>
                  <dt className="text-slate-500">오픈율 배율</dt>
                  <dd className="text-right font-bold text-amber-600">
                    {pp.ctr_multiplier != null ? `${pp.ctr_multiplier}×` : '—'}
                  </dd>
                </dl>
              </article>
            ))}
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-4 py-2 text-sm text-slate-500">
              세그먼트 → 푸시 배분 (스냅샷 {result.snapshot_at.slice(0, 16)} 기준 · run {result.run_id})
            </div>
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">세그먼트</th>
                  <th className="px-3 py-2 text-right font-medium">인원</th>
                  {result.per_push.map((pp, j) => (
                    <th key={j} className="px-3 py-2 text-right font-medium">
                      {pp.title || `푸시 ${j + 1}`}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.matrix.map((m) => (
                  <tr key={m.cluster} className="border-t border-slate-100">
                    <td className="px-3 py-2">
                      <b>
                        S{m.rank} {m.short_name}
                      </b>{' '}
                      <span className="text-xs text-slate-400">
                        원본 C{m.cluster} · {m.desc}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-slate-500">{m.size.toLocaleString()}</td>
                    {result.per_push.map((_, j) => {
                      const cell = m.allocation[j]
                      return (
                        <td key={j} className="px-3 py-2 text-right">
                          {cell ? (
                            <>
                              <b>{cell.count.toLocaleString()}</b>
                              <span className="ml-1 text-xs text-slate-400">({cell.share_pct}%)</span>
                              {cell.fallback_level === 2 && (
                                <span className="ml-0.5 text-amber-500" title="표본 부족 — 전체 통계 폴백">
                                  *
                                </span>
                              )}
                            </>
                          ) : (
                            <span className="text-slate-300">—</span>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="border-t border-slate-100 px-4 py-2 text-xs text-slate-400">
              합계: 타겟 {result.totals.target_sends.toLocaleString()}명 (전체의 {result.totals.send_pct}%)
              {hasHoldout && (
                <>
                  {' '}· 발송 {result.per_push.reduce((s, p) => s + p.csv_rows, 0).toLocaleString()} · 대조{' '}
                  {result.per_push.reduce((s, p) => s + p.holdout_rows, 0).toLocaleString()}
                </>
              )}{' '}
              · 기대 오픈 {result.totals.expected_opens.toLocaleString()}건{hasHoldout ? '(발송분)' : ''} · 전체발송 대비 클릭 커버{' '}
              {result.totals.click_coverage_pct != null ? `${result.totals.click_coverage_pct}%` : '—'} · *=표본부족 폴백
            </p>
          </section>
        </>
      )}

      <AbTestSection />
    </div>
  )
}
