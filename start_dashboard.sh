#!/usr/bin/env bash
# [참고] 상시 구동은 이제 systemd --user 서비스가 담당한다 (push-backend/frontend/jupyter,
#   enabled+linger → 로그아웃·재부팅·크래시 모두 자동 복구). 관리: systemctl --user status 'push-*'.
#   이 스크립트는 systemd 없이 수동으로 띄우는 fallback (멱등 — 이미 떠 있으면 감지만 함).
#
# grip-aidata-push 대시보드 기동 — Claude/SSH 세션과 독립(setsid 분리)으로 띄운다.
#
# 왜 8010/3001 인가:
#   포트 8000·3000 은 restart:always 도커가 점유 중 (8000=ai-models API, 3000=open-webui).
#   호스트 재부팅 때 도커는 자동 복귀하지만 이 대시보드는 자동 복귀하지 않으므로,
#   대시보드는 충돌을 피해 8010(백엔드)·3001(프론트, /api→8010 프록시)로 띄운다.
#
# 주의: setsid 로 새 세션에 분리하므로 이 스크립트를 띄운 셸/Claude 세션이 꺼져도 계속 산다.
#       (nohup 만으로는 세션 종료 시 받는 SIGTERM 을 못 막아 죽었던 이력이 있음 — 2026-06-05)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_BIN="$HOME/.local/node/bin"
BACK_PORT=8010
FRONT_PORT=3001

up() { curl -s -o /dev/null --max-time 3 "$1"; }

# --- 백엔드 (FastAPI :8010) ---
if up "http://localhost:$BACK_PORT/health"; then
  echo "✓ backend 이미 가동 중  :$BACK_PORT"
else
  setsid nohup "$ROOT/backend/.venv/bin/uvicorn" --app-dir "$ROOT/backend" app.main:app \
    --host 0.0.0.0 --port "$BACK_PORT" >/tmp/push-backend.log 2>&1 </dev/null &
  curl -s --retry 25 --retry-delay 1 --retry-connrefused --max-time 60 -o /dev/null \
    "http://localhost:$BACK_PORT/health" \
    && echo "✓ backend 기동 완료   :$BACK_PORT  (log: /tmp/push-backend.log)" \
    || { echo "✗ backend 기동 실패 — /tmp/push-backend.log 확인"; tail -20 /tmp/push-backend.log; exit 1; }
fi

# --- 프론트 (Vite :3001, /api → :8010 프록시) ---
if up "http://localhost:$FRONT_PORT/"; then
  echo "✓ frontend 이미 가동 중 :$FRONT_PORT"
else
  ( cd "$ROOT/frontend" \
    && PATH="$NODE_BIN:$PATH" FRONTEND_PORT="$FRONT_PORT" API_TARGET="http://localhost:$BACK_PORT" \
       setsid nohup "$NODE_BIN/npm" run dev -- --host 0.0.0.0 >/tmp/push-frontend.log 2>&1 </dev/null & )
  curl -s --retry 30 --retry-delay 1 --retry-connrefused --max-time 70 -o /dev/null \
    "http://localhost:$FRONT_PORT/" \
    && echo "✓ frontend 기동 완료  :$FRONT_PORT (log: /tmp/push-frontend.log)" \
    || { echo "✗ frontend 기동 실패 — /tmp/push-frontend.log 확인"; tail -20 /tmp/push-frontend.log; exit 1; }
fi

# --- JupyterLab (:8889, 노트북 루트 = 이 프로젝트) ---
JUP_PORT=8889
JUP_BIN="$HOME/oss-test/bin/jupyter"
if up "http://localhost:$JUP_PORT/lab"; then
  echo "✓ jupyter 이미 가동 중  :$JUP_PORT"
else
  setsid nohup "$JUP_BIN" lab --no-browser --ip 0.0.0.0 --port "$JUP_PORT" \
    --notebook-dir "$ROOT" >/tmp/push-jupyter.log 2>&1 </dev/null &
  curl -s --retry 40 --retry-delay 1 --retry-connrefused --max-time 90 -o /dev/null \
    "http://localhost:$JUP_PORT/lab" \
    && echo "✓ jupyter 기동 완료   :$JUP_PORT (log: /tmp/push-jupyter.log)" \
    || { echo "✗ jupyter 기동 실패 — /tmp/push-jupyter.log 확인"; tail -20 /tmp/push-jupyter.log; }
fi

echo
echo "→ 대시보드:  http://localhost:$FRONT_PORT/"
echo "→ API docs:  http://localhost:$BACK_PORT/docs"
JUP_URL="$("$JUP_BIN" server list 2>/dev/null | grep ":$JUP_PORT/" | awk '{print $1}' | head -1)"
echo "→ Jupyter:   ${JUP_URL:-(토큰은 \"$JUP_BIN server list\" 또는 /tmp/push-jupyter.log 확인)}"
echo "  (중지: pkill -f 'port $BACK_PORT' ; pkill -f 'vite --host' ; pkill -f 'port $JUP_PORT')"
