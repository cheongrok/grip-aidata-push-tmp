from fastapi import APIRouter

from app.routes.v1 import allocations, cluster_snapshot, jobs, meta, push_results

router = APIRouter(prefix="/api/v1")
router.include_router(push_results.router)
router.include_router(cluster_snapshot.router)
router.include_router(allocations.router)
router.include_router(jobs.router)
router.include_router(meta.router)
