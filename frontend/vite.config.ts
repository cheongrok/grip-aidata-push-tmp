import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 기본 3000/8000 — 포트 충돌 시 FRONTEND_PORT/API_TARGET 환경변수로 오버라이드
    port: Number(process.env.FRONTEND_PORT) || 3000,
    proxy: {
      '/api': { target: process.env.API_TARGET || 'http://localhost:8000', changeOrigin: true },
    },
  },
})
