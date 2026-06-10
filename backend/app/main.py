"""푸시 클러스터 운영 도구 백엔드 — 로컬 전용 (인증 없음).

실행: cd backend && uv run uvicorn app.main:app --reload --port 8010
문서: http://localhost:8010/docs
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))  # core.* (노트북 공유 모듈) import 용

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.routes import router  # noqa: E402

app = FastAPI(title="Push Cluster Ops")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 로컬 전용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> str:
    return "OK"
