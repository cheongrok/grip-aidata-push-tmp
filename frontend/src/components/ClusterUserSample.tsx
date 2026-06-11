import { useState } from 'react'
import { errorMessage, getClusterUserProfile } from '../services/pushService'
import type { ClusterUserProfileRes, ClusterUserSampleRow, SegmentConversionRes } from '../types/push'

const GENDER_LABEL: Record<string, string> = { M: '남', F: '여' }
const genderText = (g: string | null | undefined) => (g ? (GENDER_LABEL[g] ?? '기타') : '미상')

// 누적 시청 초 → "X분 Y초" (분만 표시하면 30초 미만이 전부 0분이라 초까지 표기)
const watchText = (sec: number) => {
  const s = Math.max(0, Math.round(sec))
  return `${Math.floor(s / 60)}분 ${s % 60}초`
}

export default function ClusterUserSample({ seg }: { seg: SegmentConversionRes }) {
  const [selected, setSelected] = useState<ClusterUserSampleRow | null>(null)
  const [profile, setProfile] = useState<ClusterUserProfileRes | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const samples = seg.cluster_user_samples ?? {}
  const hasSamples = Object.keys(samples).length > 0
  // by_cluster_total 은 rank 순(미배정 -1 마지막) — 미배정 제외하고 S1~S10 순서로 박스 구성
  const boxes = seg.by_cluster_total.filter((c) => c.cluster !== -1)

  const lookup = async () => {
    if (!selected) return
    setLoading(true)
    setError('')
    try {
      setProfile(await getClusterUserProfile(selected.user_seq))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  if (!hasSamples) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500">
        오픈 유저 샘플이 아직 없습니다. 위 <b>[집계 갱신]</b>을 누르면 세그먼트별 오픈 유저가 함께 생성됩니다.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* 선택 + 조회 바 */}
      <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
        <div className="text-sm text-slate-600">
          {selected ? (
            <>
              선택: <b>{selected.user_id}</b>
              <span className="text-slate-400"> · {selected.user_name || '-'} · 등급 {selected.grade}</span>
            </>
          ) : (
            <span className="text-slate-400">아래에서 유저(행)를 클릭해 선택하세요</span>
          )}
        </div>
        <button
          onClick={lookup}
          disabled={!selected || loading}
          className="shrink-0 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-semibold text-white shadow hover:bg-indigo-700 disabled:opacity-40"
        >
          {loading ? '조회 중…' : '조회'}
        </button>
      </div>

      {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {/* 선택 유저 프로필 (최근 90일) — 조회 버튼 바로 아래에 출력 */}
      {profile && (
        <div className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-bold text-slate-800">{profile.user_id}</span>
            <span className="text-xs text-slate-500">{profile.user_name || '-'}</span>
          </div>
          <dl className="grid grid-cols-3 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-6">
            {[
              ['등급', String(profile.grade)],
              ['성별', genderText(profile.gender)],
              ['나이', profile.age != null ? `${profile.age}세` : '미상'],
              ['총지출(90일)', `${profile.total_spend.toLocaleString()}원`],
              ['구매횟수', `${profile.purchase_count}회`],
              ['객단가', `${profile.aov.toLocaleString()}원`],
            ].map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-slate-500">{k}</dt>
                <dd className="font-semibold text-slate-700">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-3">
            <p className="mb-1 text-xs font-semibold text-slate-500">최근 90일 많이 본 방송</p>
            {profile.top_broadcasts.length === 0 ? (
              <p className="text-xs text-slate-400">유효시청 없음</p>
            ) : (
              <ul className="space-y-0.5 text-xs text-slate-600">
                {profile.top_broadcasts.map((b, i) => (
                  <li key={i} className="flex justify-between gap-2">
                    <span className="truncate">
                      📺 <b className="text-slate-700">{b.seller || '-'}</b>
                      <span className="ml-1 text-slate-400">{b.title || '-'}</span>
                    </span>
                    <span className="shrink-0 tabular-nums text-slate-500">{watchText(b.watch_sec)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {/* 클러스터별 오픈 유저 박스 (등급 내림차순) */}
      <div className="grid grid-cols-2 gap-3">
        {boxes.map((c) => {
          const rows = samples[String(c.cluster)] ?? []
          return (
            <div key={c.cluster} className="overflow-hidden rounded-xl border border-slate-200">
              <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-3 py-1.5">
                <span className="text-xs font-semibold text-slate-600">
                  S{c.rank} {c.short_name}
                  <span className="ml-1 text-[11px] font-normal text-slate-400">원본 C{c.cluster}</span>
                </span>
                <span className="text-[11px] text-slate-400">{rows.length}명</span>
              </div>
              <div className="max-h-64 overflow-y-auto">
                {rows.length === 0 ? (
                  <p className="px-3 py-4 text-center text-xs text-slate-400">오픈 유저 없음</p>
                ) : (
                  rows.map((u) => {
                    const active = selected?.user_seq === u.user_seq
                    return (
                      <button
                        key={u.user_seq}
                        onClick={() => {
                          setSelected(u)
                          setProfile(null) // 새 유저 선택 시 이전 조회 결과 비움 (조회 눌러야 갱신)
                          setError('')
                        }}
                        className={`flex w-full items-center justify-between border-b border-slate-50 px-3 py-1.5 text-left text-xs hover:bg-indigo-50 ${
                          active ? 'bg-indigo-100 font-semibold text-indigo-700' : 'text-slate-600'
                        }`}
                      >
                        <span className="truncate">
                          {u.user_id}
                          <span className="ml-1 text-slate-400">{u.user_name || '-'}</span>
                        </span>
                        <span className="ml-2 shrink-0 tabular-nums text-slate-500">등급 {u.grade}</span>
                      </button>
                    )
                  })
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
