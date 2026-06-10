from fastapi import APIRouter, HTTPException

from app.models.responses import (
    ContentMapReq,
    ContentMapRes,
    DiscoverReq,
    DiscoverRes,
    JobRes,
    PushResultsRes,
    SegmentConversionRes,
)
from app.services import jobs, push_mapping
from app.services.push_results import fetch_push_results
from app.services.segment_conversion import compute_segment_conversion, read_segment_conversion
from core.push import artifacts

router = APIRouter(tags=["push-results"])


@router.get("/push-results", response_model=PushResultsRes)
def push_results() -> PushResultsRes:
    """푸시 현황 (data_anal.push_result 전체, 최신순)."""
    return PushResultsRes(rows=fetch_push_results())


@router.post("/push-results/discover-content", response_model=DiscoverRes)
def discover_content(req: DiscoverReq) -> DiscoverRes:
    """push_seq + 판매자이름 → 발송시각 전후 방송 자동 발견 + 발송수·펀넬 미리보기·경고."""
    return DiscoverRes(**push_mapping.discover_content(req.push_seq, req.user_name))


@router.post("/push-results/content-map", response_model=ContentMapRes)
def save_content_map(req: ContentMapReq) -> ContentMapRes:
    """발견된 방송을 커버리지 매핑(push_content_id_map.json)에 저장 — 이후 [집계 갱신] 시 반영."""
    if not req.content_ids:
        raise HTTPException(422, "content_ids 를 1개 이상 입력하세요.")
    allowed = set(artifacts.categories())
    if req.category not in allowed:
        raise HTTPException(422, f"허용되지 않은 카테고리입니다: '{req.category}' (허용: {sorted(allowed)})")
    push_mapping.upsert_content_id_map(
        req.push_seq, req.content_ids, req.category, req.seller_name, req.title
    )
    return ContentMapRes(ok=True, push_seq=req.push_seq)


@router.get("/push-results/segment-conversion", response_model=SegmentConversionRes)
def segment_conversion() -> SegmentConversionRes:
    """세그먼트(클러스터)별 전환율 — 캐시된 집계 결과. 없으면 404."""
    res = read_segment_conversion()
    if res is None:
        raise HTTPException(404, "집계 없음 — 먼저 집계를 실행하세요.")
    return SegmentConversionRes(**res)


@router.post("/push-results/segment-conversion/refresh", response_model=JobRes)
def refresh_segment_conversion() -> JobRes:
    """세그먼트별 전환율 재집계 (수기 확정 push→방송 매핑 기반 전환 집계, Snowflake 비동기 job)."""
    job_id = jobs.start_exclusive("segment_conversion", compute_segment_conversion)
    if job_id is None:
        raise HTTPException(409, "이미 집계 작업이 실행 중입니다.")
    return JobRes(**jobs.get_job(job_id))
