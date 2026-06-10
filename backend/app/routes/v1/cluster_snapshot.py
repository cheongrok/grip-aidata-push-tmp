from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.responses import JobRes, SnapshotRes
from app.services import jobs, snapshot
from core.push.features import refresh_snapshot

router = APIRouter(tags=["cluster-snapshot"])


@router.get("/cluster-snapshot", response_model=SnapshotRes)
def get_snapshot() -> SnapshotRes:
    """최신 유저 클러스터 스냅샷 (클러스터 카드 10개). 없으면 404."""
    res = snapshot.snapshot_response()
    if res is None:
        raise HTTPException(404, "스냅샷 없음 — '유저 클러스터 최신화하기'를 먼저 실행하세요.")
    return SnapshotRes(**res)


class RefreshReq(BaseModel):
    n_day: int = Field(default=180, ge=7, le=730, description="최근 N일 시청자 기준 모수")


@router.post("/cluster-snapshot/refresh", response_model=JobRes)
def refresh(req: RefreshReq) -> JobRes:
    """발송가능 전체 모수 → 현재시점 피처 → 클러스터 배정 (Snowflake 약 3~15분, 비동기 job)."""
    job_id = jobs.start_exclusive("snapshot", refresh_snapshot, n_day=req.n_day)
    if job_id is None:
        raise HTTPException(409, "이미 최신화 작업이 실행 중입니다.")
    return JobRes(**jobs.get_job(job_id))
