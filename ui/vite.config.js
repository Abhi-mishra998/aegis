import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// AEGIS_BACKEND lets the Sprint 4 e2e runner point the dev server at
// dev.aegisagent.in while still exercising the Sprint 4 UI code in this
// working copy. Default localhost preserves the existing on-laptop
// docker-compose workflow exactly as it was.
const BACKEND = process.env.AEGIS_BACKEND || 'http://localhost:8000'

// All paths served by the gateway — proxied same-origin so cookies and CORS
// are never an issue in dev. In production the nginx container handles routing.
// Keep this list in sync with the regex in ui/nginx.conf.
const API_PATHS = [
  '/auth', '/agents', '/dashboard', '/audit', '/billing', '/risk',
  '/decision', '/forensics', '/incidents', '/api-keys', '/auto-response',
  '/insights', '/system', '/execute', '/events', '/health', '/policy',
  '/usage', '/logs', '/stream',
  // Paths added 2026-05-24 — were in nginx but missing from vite proxy,
  // breaking IdentityGraph, FlightRecorder, AutonomyContracts, receipts,
  // transparency, and tenant quota in dev mode.
  '/graph', '/flight', '/autonomy', '/tenant', '/receipts', '/transparency',
]

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: 'localhost',
    open: true,
    proxy: Object.fromEntries(
      API_PATHS.map((path) => [
        path,
        { target: BACKEND, changeOrigin: true, secure: false },
      ])
    ),
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
