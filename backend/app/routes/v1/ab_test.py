from fastapi import APIRouter, HTTPException

from app.models.responses import ABVerifyReq, ABVerifyRes
from app.services import ab_test

router = APIRouter(tags=["ab-test"])


@router.post("/ab-test/verify", response_model=ABVerifyRes)
def ab_verify(req: ABVerifyReq) -> ABVerifyRes:
    """푸시알림 효과검증 — 발송군 vs 대조군의 매핑 방송 유효시청·구매 증분(A/B 홀드아웃)."""
    try:
        res = ab_test.verify(
            req.push_seq, req.content_seqs,
            req.treatment_user_seqs, req.treatment_clusters,
            req.control_user_seqs, req.control_clusters, req.days,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return ABVerifyRes(**res)
