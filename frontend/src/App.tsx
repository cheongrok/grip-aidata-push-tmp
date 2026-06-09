import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import AllocationPage from './pages/AllocationPage'
import ClusterSnapshotPage from './pages/ClusterSnapshotPage'
import PushResultsPage from './pages/PushResultsPage'

const NAV = [
  { to: '/results', label: '푸시 현황', desc: '발송 결과 집계' },
  { to: '/clusters', label: '유저 클러스터', desc: '최신화 & 프로필' },
  { to: '/allocation', label: '푸시 분배', desc: '타겟 추출 & CSV' },
]

export default function App() {
  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r border-slate-200 bg-white px-4 py-6">
        <h1 className="mb-8 px-2 text-lg font-bold text-slate-800">
          푸시 마케팅 효율화
          <span className="block text-xs font-normal text-slate-400">운영 도구 (gpu2.grip.studio)</span>
        </h1>
        <nav className="space-y-1">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `block rounded-lg px-3 py-2 text-sm transition ${
                  isActive ? 'bg-indigo-50 font-semibold text-indigo-700' : 'text-slate-600 hover:bg-slate-50'
                }`
              }
            >
              {n.label}
              <span className="block text-[11px] font-normal text-slate-400">{n.desc}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="flex-1 px-8 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/results" replace />} />
          <Route path="/results" element={<PushResultsPage />} />
          <Route path="/clusters" element={<ClusterSnapshotPage />} />
          <Route path="/allocation" element={<AllocationPage />} />
        </Routes>
      </main>
    </div>
  )
}
