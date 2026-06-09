# syntax=docker/dockerfile:1
# 프론트엔드 (React/Vite) — 정적 빌드 후 nginx 로 서빙(+ /api 를 백엔드로 프록시).
# 빌드 컨텍스트는 repo 루트.

# ---- 1) 빌드 ----
FROM node:22-alpine AS build
WORKDIR /app
# 락파일 먼저 복사 → 의존성 레이어 캐시
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # tsc --noEmit && vite build → /app/dist

# ---- 2) 서빙 (nginx) ----
FROM nginx:alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 3001
# nginx:alpine 기본 CMD(포그라운드) 사용
