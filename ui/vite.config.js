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
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        // Split heavy vendor libs into their own chunks so /dashboard FCP
        // doesn't pay for reactflow + recharts + livekit that only fire on
        // /identity-graph, /agent-cost, /voice-agent respectively.
        manualChunks: (id) => {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('reactflow') || id.includes('@reactflow')) return 'vendor-reactflow'
          if (id.includes('recharts') || id.includes('d3-')) return 'vendor-charts'
          if (id.includes('@livekit') || id.includes('livekit-client')) return 'vendor-livekit'
          if (id.includes('@clerk')) return 'vendor-clerk'
          if (id.includes('lucide-react')) return 'vendor-icons'
          if (id.includes('react-router')) return 'vendor-router'
          if (id.includes('/react/') || id.includes('/react-dom/') || id.includes('/scheduler/')) return 'vendor-react'
          return 'vendor'
        },
      },
    },
  },
})
