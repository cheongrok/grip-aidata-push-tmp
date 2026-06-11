from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.models.responses import AllocationReq, AllocationRes
from app.services import allocation_svc
from core.push import artifacts

router = APIRouter(tags=["allocations"])


@router.post("/allocations", response_model=AllocationRes)
def create(req: AllocationReq) -> AllocationRes:
    """다중 푸시 분배 — 클러스터 매칭 배분 + 푸시별 user_id CSV 생성 (로컬 계산, 즉시 응답).

    수동 타겟: 각 푸시에 clusters(원본 cluster id) 지정 시 그 클러스터에만 발송.
    holdout_pct>0: 각 푸시 발송 대상에서 무작위로 그 비율을 대조군(미발송)으로 분리 → A/B 측정용.
    """
    if not req.pushes:
        raise HTTPException(422, "푸시를 1개 이상 입력하세요.")
    if req.volumes is not None and (len(req.volumes) != len(req.pushes) or abs(sum(req.volumes) - 1) > 1e-9):
        raise HTTPException(422, "volumes 는 푸시 수와 같고 합이 1이어야 해요.")
    has = [bool(p.clusters) for p in req.pushes]
    if any(has) and not all(has):
        raise HTTPException(422, "수동 타겟 모드: 모든 푸시에 세그먼트를 선택하거나, 전부 비워 자동 모드로 두세요.")
    if any(has) and req.volumes is not None:
        raise HTTPException(422, "수동 타겟 모드에서는 volumes 를 쓸 수 없어요 (세그먼트 선택으로 분배가 정해져요).")
    valid = set(artifacts.load()["MAY_ORDER"])
    for p in req.pushes:
        if p.clusters and not set(p.clusters) <= valid:
            raise HTTPException(422, f"유효하지 않은 세그먼트 id 가 있어요 (허용: {sorted(valid)}).")
    try:
        res = allocation_svc.create_allocation(
            [p.model_dump() for p in req.pushes], top_k=req.top_k, volumes=req.volumes,
            seed=req.seed, holdout_pct=req.holdout_pct,
        )
    except allocation_svc.SnapshotMissingError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return AllocationRes(**res)


@router.get("/allocations/{run_id}/download/{push_no}")
def download(run_id: str, push_no: int, arm: Literal["treatment", "control"] = "treatment") -> FileResponse:
    """푸시별 발송 대상 CSV (user_id, user_seq, cluster). arm=control 이면 대조군 CSV."""
    p = allocation_svc.csv_path(run_id, push_no, arm)
    if p is None:
        raise HTTPException(404, "CSV 없음 — 분배를 먼저 실행하세요.")
    suffix = "_control" if arm == "control" else ""
    return FileResponse(p, media_type="text/csv", filename=f"allocation_{run_id}_push{push_no}{suffix}.csv")
