import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 기본 3000/8000 — 포트 충돌 시 FRONTEND_PORT/API_TARGET 환경변수로 오버라이드
    port: Number(process.env.FRONTEND_PORT) || 3000,
    // 도메인 접속 허용 (vite dev 는 기본적으로 localhost/IP 외 호스트를 403 차단) — gpu2 내부망 운영용
    allowedHosts: ['gpu2.grip.studio', '.grip.studio'],
    proxy: {
      '/api': { target: process.env.API_TARGET || 'http://localhost:8000', changeOrigin: true },
    },
  },
})
