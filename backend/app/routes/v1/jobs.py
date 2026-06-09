from fastapi import APIRouter, HTTPException

from app.models.responses import JobRes
from app.services import jobs as jobs_svc

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobRes)
def get_job(job_id: str) -> JobRes:
    j = jobs_svc.get_job(job_id)
    if j is None:
        raise HTTPException(404, "job 없음 (서버 재시작으로 소실됐을 수 있음 — 결과 파일은 유지됨)")
    return JobRes(**j)
