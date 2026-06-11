import { useCallback, useEffect, useState } from 'react'
import ClusterUserSample from '../components/ClusterUserSample'
import SegmentShareChart from '../components/SegmentShareChart'
import { useJobPolling } from '../hooks/useJobPolling'
import {
  errorMessage,
  getSegmentConversion,
  getSnapshot,
  startRefresh,
  startSegmentConversionRefresh,
} from '../services/pushService'
import type { SegmentConversionRes, SnapshotRes } from '../types/push'

const TIER_STYLE: Record<string, string> = {
  Tier1: 'bg-emerald-100 text-emerald-700',
  Tier2: 'bg-amber-100 text-amber-700',
  Tier3: 'bg-red-100 text-red-700',
}

export default function ClusterSnapshotPage() {
  const [snap, setSnap] = useState<SnapshotRes | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [nDay, setNDay] = useState(180)
  const [jobId, setJobId] = useState<string | null>(null)

  const load = useCallback(() => {
    getSnapshot()
      .then(setSnap)
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoaded(true))
  }, [])

  useEffect(load, [load])

  const job = useJobPolling(jobId, (j) => {
    if (j.status === 'done') load()
    setJobId(null)
    if (j.status === 'error') setError(j.error ?? '최신화 실패')
  })

  const refresh = async () => {
    setError('')
    try {
      const j = await startRefresh(nDay)
      setJobId(j.job_id)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const running = jobId !== null

  // 세그먼트별 전환(오픈 점유 vs 구매 기여) — 클러스터 최신화와 별개로 자체 [집계 갱신] 버튼 사용
  const [seg, setSeg] = useState<SegmentConversionRes | null>(null)
  const [segError, setSegError] = useState('')
  const [segJobId, setSegJobId] = useState<string | null>(null)

  const loadSeg = useCallback(() => {
    getSegmentConversion()
      .then(setSeg)
      .catch((e) => setSegError(errorMessage(e)))
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

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-800">유저 세그먼트</h2>
          <p className="text-sm text-slate-500">
            발송가능 전체 모수(마케팅 기준)를 현재 시점 행동 피처로 10개 세그먼트에 배정합니다
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="flex items-center gap-2">
            <label className="text-sm text-slate-500">
              최근{' '}
              <input
                type="number"
                value={nDay}
                min={7}
                max={730}
                onChange={(e) => setNDay(+e.target.value)}
                className="w-20 rounded-lg border border-slate-300 px-2 py-1 text-right text-sm"
              />{' '}
              일 시청자
            </label>
            <button
              onClick={refresh}
              disabled={running}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-indigo-700 disabled:opacity-40"
            >
              {running ? '최신화 중…' : '유저 세그먼트 최신화하기'}
            </button>
          </div>
          <p className="text-xs text-slate-400">회당 Snowflake 5~15분 소요 · 푸시 발송 전 갱신 권장</p>
        </div>
      </header>

      {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {running && (
        <div className="flex items-center gap-3 rounded-xl border border-indigo-200 bg-indigo-50 p-4">
          <span className="h-3 w-3 animate-ping rounded-full bg-indigo-500" />
          <div className="text-sm text-indigo-800">
            <b>{job?.stage ?? '시작 중…'}</b>
            <span className="ml-2 text-indigo-500">Snowflake 적재 포함 5~15분 — 페이지를 떠나도 작업은 계속됩니다</span>
          </div>
        </div>
      )}

      {loaded && !snap && !running && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
          아직 스냅샷이 없습니다. <b>[유저 세그먼트 최신화하기]</b>를 눌러 첫 스냅샷을 만드세요.
          <p className="mt-1 text-xs">회당 Snowflake 5~15분 소요 · 푸시 발송 전 갱신 권장</p>
        </div>
      )}

      {snap && (
        <>
          <div className="flex items-center gap-4 text-sm">
            <span className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 font-semibold text-white shadow-sm">
              <span className="h-2 w-2 rounded-full bg-emerald-300" />
              적용 중
            </span>
            <span className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-indigo-800 shadow-sm">
              총 <b>{snap.n_users.toLocaleString()}</b>명
            </span>
            <span className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-indigo-800 shadow-sm">
              기준 시점 <b>{snap.snapshot_at.slice(0, 16)}</b>
            </span>
            <span className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-indigo-800 shadow-sm">
              최근 <b>{snap.n_day}</b>일 시청자 · {Math.round(snap.duration_sec / 60)}분 소요
            </span>
            {snap.stale && (
              <span className="rounded-lg bg-red-100 px-3 py-1.5 font-semibold text-red-700">
                ⚠ 7일 경과 — 발송 전 최신화 권장
              </span>
            )}
          </div>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">세그먼트</th>
                  <th className="px-3 py-2 text-left font-medium">Tier</th>
                  <th className="px-3 py-2 text-right font-medium">인원</th>
                  <th className="px-3 py-2 text-left font-medium">비중</th>
                  <th className="px-3 py-2 text-right font-medium">5월 CTR</th>
                  <th className="px-3 py-2 text-right font-medium">lift</th>
                  <th className="px-3 py-2 text-left font-medium">특징</th>
                </tr>
              </thead>
              <tbody>
                {snap.clusters.map((c) => (
                  <tr key={c.cluster} className="border-t border-slate-100 align-top hover:bg-slate-50">
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <div className="font-bold text-slate-700">
                        S{c.rank} {c.short_name}
                      </div>
                      <div className="text-xs text-slate-400">원본 C{c.cluster}</div>
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${TIER_STYLE[c.tier]}`}>
                        {c.tier_label}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{c.count.toLocaleString()}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 shrink-0 overflow-hidden rounded bg-slate-100">
                          <div className="h-full rounded bg-indigo-400" style={{ width: `${Math.min(c.share_pct * 4, 100)}%` }} />
                        </div>
                        <span className="text-xs tabular-nums text-slate-500">{c.share_pct}%</span>
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{c.may_ctr_pct}%</td>
                    <td className="px-3 py-2.5 text-right">
                      <b className={c.may_lift >= 1 ? 'text-emerald-600' : 'text-slate-500'}>{c.may_lift}</b>
                    </td>
                    <td className="px-3 py-2.5 text-slate-600">
                      <div className="text-[11px] text-slate-400">{c.short_desc}</div>
                      <div className="leading-snug">{c.friendly_desc}</div>
                      {c.action && <div className="mt-1 font-semibold text-indigo-700">→ {c.action}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-end justify-between">
              <div>
                <h3 className="text-sm font-semibold text-slate-600">세그먼트별 오픈 점유 vs 구매 기여</h3>
                <p className="text-xs text-slate-400">
                  실측 전환(수기 매핑 기준)으로 본 세그먼트 등급 검증 — 상위가 구매를 만들고 하위는 오픈만 차지하는지.
                  {seg && (
                    <> · 집계 {seg.computed_at.replace('T', ' ').slice(0, 16)} · 스냅샷 기준 {seg.cluster_snapshot_at.slice(0, 16)}</>
                  )}
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
                <p className="text-xs text-slate-400">수 분 소요 · 세그먼트 최신화와 별개</p>
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

            {seg && seg.cluster_snapshot_at.slice(0, 16) < snap.snapshot_at.slice(0, 16) && (
              <p className="mb-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                ⚠ 이 차트는 이전 세그먼트 스냅샷({seg.cluster_snapshot_at.slice(0, 16)}) 기준입니다. 현재
                스냅샷({snap.snapshot_at.slice(0, 16)})에 맞추려면 <b>[집계 갱신]</b>을 누르세요.
              </p>
            )}

            {seg ? (
              <SegmentShareChart clusters={seg.by_cluster_total} overall={seg.totals} />
            ) : (
              !segRunning && (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500">
                  아직 전환 집계가 없습니다. <b>[집계 실행]</b>을 눌러 계산하세요 (수 분 소요).
                </div>
              )
            )}
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="mb-3">
              <h3 className="text-sm font-semibold text-slate-600">세그먼트 내 유저 샘플 분석</h3>
              <p className="text-xs text-slate-400">
                각 세그먼트에서 푸시를 <b>오픈</b>한 유저(운영자 제외)를 등급순으로 표시 — 행을 선택하고 [조회]로 프로필(최근 90일)을 확인하세요.
              </p>
            </div>
            {seg ? (
              <ClusterUserSample seg={seg} />
            ) : (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500">
                위 <b>[집계 실행]</b>을 누르면 세그먼트별 오픈 유저 샘플이 함께 생성됩니다.
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
