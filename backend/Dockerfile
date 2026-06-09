# syntax=docker/dockerfile:1
# 백엔드 (FastAPI) — 빌드 컨텍스트는 repo 루트 (backend/ + 공유 core/ 둘 다 필요).
# 이미지엔 코드만 담는다. 데이터/모델/비밀(artifacts·cache·.env·.p8)은 런타임 볼륨으로 마운트.

# ---- 1) 의존성 설치 (uv, lock 고정) ----
FROM python:3.11-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
# pyproject/uv.lock 만 먼저 복사 → 의존성 레이어 캐시 (소스 변경 시 재설치 안 함)
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- 2) 런타임 ----
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"
# 설치된 의존성(venv)만 복사 — uv 자체는 런타임 이미지에 안 들어감
COPY --from=builder /opt/venv /opt/venv
# 앱 코드 + 공유 core 패키지
WORKDIR /app/backend
COPY backend/ /app/backend/
COPY core/ /app/core/
EXPOSE 8010
# main.py 가 parents[2]=/app 를 sys.path 에 넣어 core.* 를 import 한다.
# artifacts/·cache/·.env·.p8 는 docker-compose 볼륨으로 /app 아래에 마운트된다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
