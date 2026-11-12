import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const repoRoot = fileURLToPath(new URL('..', import.meta.url))

function parsePort(name: string, value: string | undefined, fallback: number): number {
  if (value === undefined) return fallback

  const port = Number(value)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${name} must be an integer between 1 and 65535`)
  }
  return port
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, [
    'API_PORT',
    'FRONTEND_PORT',
    'API_PROXY_TARGET',
  ])
  const apiPort = parsePort('API_PORT', process.env.API_PORT ?? env.API_PORT, 8000)
  const frontendPort = parsePort(
    'FRONTEND_PORT',
    process.env.FRONTEND_PORT ?? env.FRONTEND_PORT,
    5173,
  )
  const proxyTarget = (process.env.API_PROXY_TARGET ?? env.API_PROXY_TARGET)?.trim()
    || `http://127.0.0.1:${apiPort}`

  return {
    envDir: repoRoot,
    plugins: [react(), tailwindcss()],
    server: {
      port: frontendPort,
      strictPort: true,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
        '/socket.io': {
          target: proxyTarget,
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }
})
