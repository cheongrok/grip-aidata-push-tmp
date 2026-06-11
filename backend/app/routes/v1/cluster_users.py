from fastapi import APIRouter, HTTPException

from app.models.responses import ClusterUserProfileRes
from app.services.cluster_users import fetch_user_profile

router = APIRouter(tags=["cluster-users"])


@router.get("/cluster-users/profile/{user_seq}", response_model=ClusterUserProfileRes)
def cluster_user_profile(user_seq: int) -> ClusterUserProfileRes:
    """선택 유저의 기본 프로필/행동 요약 (등급·성별·나이·총지출·구매횟수·객단가·최근 시청 상위방송). 없으면 404."""
    res = fetch_user_profile(user_seq)
    if res is None:
        raise HTTPException(404, "해당 유저를 찾을 수 없어요.")
    return ClusterUserProfileRes(**res)
