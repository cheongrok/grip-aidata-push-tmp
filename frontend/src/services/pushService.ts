import axios from 'axios'
import type {
  AbVerifyReq,
  AbVerifyRes,
  AllocationRes,
  DiscoverRes,
  JobRes,
  MetaRes,
  PushInput,
  PushResultRow,
  SegmentConversionRes,
  SnapshotRes,
} from '../types/push'

// /api 프록시 — dev: vite → env API_TARGET (기본 localhost:8000, systemd 운영 서비스는 8010) · 배포: nginx → backend:8010
const api = axios.create({ baseURL: '', timeout: 60_000, headers: { 'Content-Type': 'application/json' } })

export function errorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = e.response?.data?.detail
    if (Array.isArray(detail)) {
      // FastAPI 422 검증 에러: [{loc, msg, type}, ...]
      return detail.map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).join('.')}: ${d.msg}`).join(' / ')
    }
    if (detail) return String(detail)
    if (e.code === 'ERR_NETWORK') return '백엔드에 연결할 수 없습니다.'
    return `요청 실패 (${e.response?.status ?? e.code})`
  }
  return String(e)
}

export async function getMeta(): Promise<MetaRes> {
  return (await api.get('/api/v1/meta')).data
}

export async function getPushResults(): Promise<PushResultRow[]> {
  return (await api.get('/api/v1/push-results')).data.rows
}

export async function discoverContent(pushSeq: number, userName: string): Promise<DiscoverRes> {
  return (await api.post('/api/v1/push-results/discover-content', { push_seq: pushSeq, user_name: userName })).data
}

export async function saveContentMap(
  pushSeq: number,
  contentIds: string[],
  category: string,
  sellerName: string,
  title: string | null,
): Promise<{ ok: boolean; push_seq: number }> {
  return (
    await api.post('/api/v1/push-results/content-map', {
      push_seq: pushSeq,
      content_ids: contentIds,
      category,
      seller_name: sellerName,
      title,
    })
  ).data
}

export async function getSnapshot(): Promise<SnapshotRes | null> {
  try {
    return (await api.get('/api/v1/cluster-snapshot')).data
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status === 404) return null
    throw e
  }
}

export async function startRefresh(nDay: number): Promise<JobRes> {
  return (await api.post('/api/v1/cluster-snapshot/refresh', { n_day: nDay })).data
}

export async function getJob(jobId: string): Promise<JobRes> {
  return (await api.get(`/api/v1/jobs/${jobId}`)).data
}

export async function createAllocation(
  pushes: PushInput[],
  topK: number,
  seed: number,
  holdoutPct = 0,
): Promise<AllocationRes> {
  return (await api.post('/api/v1/allocations', { pushes, top_k: topK, seed, holdout_pct: holdoutPct })).data
}

export async function getSegmentConversion(): Promise<SegmentConversionRes | null> {
  try {
    return (await api.get('/api/v1/push-results/segment-conversion')).data
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status === 404) return null
    throw e
  }
}

export async function startSegmentConversionRefresh(): Promise<JobRes> {
  return (await api.post('/api/v1/push-results/segment-conversion/refresh')).data
}

export async function verifyAbTest(payload: AbVerifyReq): Promise<AbVerifyRes> {
  return (await api.post('/api/v1/ab-test/verify', payload)).data
}
