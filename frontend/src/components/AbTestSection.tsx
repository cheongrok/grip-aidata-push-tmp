import { useState } from 'react'
import { errorMessage, verifyAbTest } from '../services/pushService'
import type { AbMetric, AbVerifyRes } from '../types/push'

// CSV(USER_ID,USER_SEQ,CLUSTER) 파싱 — 헤더로 컬럼 위치 찾기, USER_SEQ 필수
function parseCsv(text: string): { user_seqs: number[]; clusters: number[] } {
  const lines = text.replace(/^﻿/, '').trim().split(/\r?\n/)
  if (lines.length < 2) throw new Error('빈 CSV 입니다')
  const header = lines[0].split(',').map((h) => h.trim().toUpperCase())
  const iSeq = header.indexOf('USER_SEQ')
  const iClu = header.indexOf('CLUSTER')
  if (iSeq < 0) throw new Error('CSV에 USER_SEQ 컬럼이 없습니다')
  const user_seqs: number[] = []
  const clusters: number[] = []
  for (let k = 1; k < lines.length; k++) {
    const cols = lines[k].split(',')
    const s = Number(cols[iSeq])
    if (!Number.isFinite(s)) continue
    user_seqs.push(s)
    clusters.push(iClu >= 0 ? Number(cols[iClu]) : -1)
  }
  return { user_seqs, clusters }
}

type Parsed = { user_seqs: number[]; clusters: number[]; name: string }

const pct = (v: number) => `${v.toFixed(3)}%`
const won = (v: number) => `₩${v.toLocaleString()}`

// 증분 + 유의성 한 칸 (양·유의=초록, 음·유의=빨강, 무의미=회색)
function LiftCell({ m }: { m: AbMetric }) {
  const sig = m.significant
  const color = !sig ? 'text-slate-400' : m.lift_pp > 0 ? 'text-emerald-600' : 'text-red-600'
  return (
    <span className={`font-bold ${color}`}>
      {m.lift_pp > 0 ? '+' : ''}
      {m.lift_pp.toFixed(3)}%p
      <span className="ml-1 text-xs font-normal text-slate-400">
        {m.p_value == null ? '' : `p=${m.p_value}`} {sig ? '★유의' : ''}
      </span>
    </span>
  )
}

export default function AbTestSection() {
  const [pushSeq, setPushSeq] = useState('')
  const [contentSeqs, setContentSeqs] = useState('')
  const [days, setDays] = useState('')
  const [treat, setTreat] = useState<Parsed | null>(null)
  const [ctrl, setCtrl] = useState<Parsed | null>(null)
  const [res, setRes] = useState<AbVerifyRes | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const loadFile = async (file: File | undefined, set: (p: Parsed | null) => void) => {
    setError('')
    if (!file) return set(null)
    try {
      const p = parseCsv(await file.text())
      set({ ...p, name: file.name })
    } catch (e) {
      set(null)
      setError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const csList = contentSeqs
    .split(/[\s,]+/)
    .map((s) => Number(s))
    .filter((n) => Number.isInteger(n) && n > 0)
  const canRun = Number(pushSeq) >= 1 && csList.length > 0 && treat && ctrl && !busy

  const run = async () => {
    if (!treat || !ctrl) return
    setError('')
    setBusy(true)
    setRes(null)
    try {
      setRes(
        await verifyAbTest({
          push_seq: Number(pushSeq),
          content_seqs: csList,
          days: days ? Number(days) : null,
          treatment_user_seqs: treat.user_seqs,
          treatment_clusters: treat.clusters,
          control_user_seqs: ctrl.user_seqs,
          control_clusters: ctrl.clusters,
        }),
      )
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
      <div>
        <h3 className="text-sm font-semibold text-slate-700">푸시알림 효과검증 (A/B)</h3>
        <p className="text-xs leading-relaxed text-slate-400">
          위에서 만든 <b>발송 CSV·대조군 CSV</b>를 올리고 <b>push_seq·content_seq</b>를 입력하면, 발송 이후 그 방송의{' '}
          <b>유효시청·구매·GMV</b>를 두 그룹에서 집계해 <b>증분(발송−대조)</b>과 유의성(z검정)을 냅니다. 발송 며칠 뒤 실행하세요.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm text-slate-500">
          push_seq{' '}
          <input
            type="number"
            min={1}
            value={pushSeq}
            onChange={(e) => setPushSeq(e.target.value)}
            className="w-28 rounded-lg border border-slate-300 px-2 py-1 text-right text-sm"
          />
        </label>
        <label className="text-sm text-slate-500">
          content_seq(쉼표){' '}
          <input
            value={contentSeqs}
            onChange={(e) => setContentSeqs(e.target.value)}
            placeholder="예: 2073979, 2074001"
            className="w-52 rounded-lg border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="text-sm text-slate-500" title="발송시각 + N일까지 측정. 비우면 현재까지">
          측정기간(일){' '}
          <input
            type="number"
            min={1}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            placeholder="현재까지"
            className="w-24 rounded-lg border border-slate-300 px-2 py-1 text-right text-sm"
          />
        </label>
      </div>

      <div className="flex flex-wrap gap-4">
        {(
          [
            ['발송 CSV (treatment)', treat, setTreat],
            ['대조군 CSV (control)', ctrl, setCtrl],
          ] as const
        ).map(([label, val, set]) => (
          <label key={label} className="flex flex-col gap-1 text-sm text-slate-500">
            <span>{label}</span>
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => loadFile(e.target.files?.[0], set)}
              className="text-xs file:mr-2 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-slate-600"
            />
            {val && <span className="text-xs text-emerald-600">✓ {val.user_seqs.length.toLocaleString()}명</span>}
          </label>
        ))}
        <button
          onClick={run}
          disabled={!canRun}
          className="self-end rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white shadow hover:bg-indigo-700 disabled:opacity-40"
        >
          {busy ? '검증 중…' : '효과검증'}
        </button>
      </div>

      {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {res && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1">
              push <b>{res.push_seq}</b> · content {res.content_seqs.join(', ')}
            </span>
            <span className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1">
              측정 <b>{res.period_start.slice(5, 16)} ~ {res.period_end.slice(5, 16)}</b>
            </span>
            <span className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1">
              발송 <b>{res.treatment.n_users.toLocaleString()}</b> · 대조 <b>{res.control.n_users.toLocaleString()}</b>
            </span>
          </div>

          {res.warnings.map((w, i) => (
            <p key={i} className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              ⚠ {w}
            </p>
          ))}

          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left font-medium">지표</th>
                <th className="px-3 py-2 text-right font-medium">발송군</th>
                <th className="px-3 py-2 text-right font-medium">대조군</th>
                <th className="px-3 py-2 text-right font-medium">증분 (발송−대조)</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-slate-100">
                <td className="px-3 py-2">유효시청 전환율</td>
                <td className="px-3 py-2 text-right tabular-nums">{pct(res.watch.treatment_pct)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{pct(res.watch.control_pct)}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  <LiftCell m={res.watch} />
                </td>
              </tr>
              <tr className="border-t border-slate-100">
                <td className="px-3 py-2">구매 전환율</td>
                <td className="px-3 py-2 text-right tabular-nums">{pct(res.purchase.treatment_pct)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{pct(res.purchase.control_pct)}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  <LiftCell m={res.purchase} />
                </td>
              </tr>
              <tr className="border-t border-slate-100">
                <td className="px-3 py-2">GMV / 1인</td>
                <td className="px-3 py-2 text-right tabular-nums">{won(res.gmv_per_user_treatment)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{won(res.gmv_per_user_control)}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  <span className={`font-bold ${res.gmv_per_user_lift >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                    {res.gmv_per_user_lift >= 0 ? '+' : ''}
                    {won(res.gmv_per_user_lift)}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>

          {res.by_cluster.length > 0 && (
            <div className="overflow-hidden rounded-lg border border-slate-100">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-3 py-1.5 text-left font-medium">세그먼트</th>
                    <th className="px-3 py-1.5 text-right font-medium">발송 (n · 구매율)</th>
                    <th className="px-3 py-1.5 text-right font-medium">대조 (n · 구매율)</th>
                    <th className="px-3 py-1.5 text-right font-medium">구매 증분</th>
                  </tr>
                </thead>
                <tbody>
                  {res.by_cluster.map((c) => {
                    const sig = c.p_value != null && c.p_value < 0.05
                    return (
                      <tr key={c.cluster} className="border-t border-slate-100">
                        <td className="px-3 py-1.5">
                          {c.cluster === -1 ? '미배정' : `S${c.rank} ${c.short_name}`}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums">
                          {c.t_users.toLocaleString()} · {c.t_purchase_rate_pct}%
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-slate-500">
                          {c.c_users.toLocaleString()} · {c.c_purchase_rate_pct}%
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums">
                          <span className={!sig ? 'text-slate-400' : c.lift_pp > 0 ? 'text-emerald-600 font-semibold' : 'text-red-600 font-semibold'}>
                            {c.lift_pp > 0 ? '+' : ''}
                            {c.lift_pp}%p{c.p_value != null ? ` (p=${c.p_value})` : ''}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          <p className="text-xs leading-relaxed text-slate-400">
            증분이 <b>+이고 ★유의(p&lt;0.05)</b> 면 그 푸시가 실제로 시청·구매를 추가로 일으킨 것입니다. 구매는 희귀해
            단일 푸시·작은 세그먼트는 유의하기 어려우니, 1차 지표는 <b>유효시청</b>으로 보고 여러 푸시를 누적하세요.
            (발송군 전체 vs 대조군 전체 비교 — 오픈자만 따로 보면 안 됩니다.)
          </p>
        </div>
      )}
    </section>
  )
}
