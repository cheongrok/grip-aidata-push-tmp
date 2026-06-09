from fastapi import APIRouter

from app.models.responses import MetaRes, SnapshotStatus
from app.services import snapshot
from core.push import artifacts

router = APIRouter(tags=["meta"])


@router.get("/meta", response_model=MetaRes)
def meta() -> MetaRes:
    """카테고리/시간대 셀렉터 옵션 + 스냅샷 상태."""
    m = snapshot.read_meta()
    status = SnapshotStatus(exists=False)
    if m is not None:
        status = SnapshotStatus(
            exists=True, snapshot_at=m["snapshot_at"], n_users=m["n_users"], stale=snapshot.is_stale(m)
        )
    return MetaRes(
        categories=artifacts.categories(),
        hour_buckets=artifacts.hour_buckets(),
        may_order=artifacts.load()["MAY_ORDER"],
        snapshot=status,
    )
