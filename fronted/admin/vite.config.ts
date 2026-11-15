import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'
const backend = process.env.XA_API_PROXY || 'http://127.0.0.1:18106'
const adminEntryHost = process.env.XA_ADMIN_ENTRY_HOST?.trim()
const proxyTarget = {
  target: backend,
  changeOrigin: !adminEntryHost,
  ...(adminEntryHost ? { headers: { Host: adminEntryHost } } : {}),
}
export default defineConfig({
  base: process.env.XA_BASE_PATH || '/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': resolve(import.meta.dirname, 'src') }, dedupe: ['react', 'react-dom'] },
  server: {
    host: process.env.XA_DEV_HOST || '127.0.0.1', port: Number(process.env.XA_DEV_PORT || 13103), strictPort: true,
    allowedHosts: ['admin.esports.club', 'localhost', '127.0.0.1'],
    watch: { usePolling: true, interval: 150, awaitWriteFinish: { stabilityThreshold: 200, pollInterval: 50 } },
    fs: { allow: [resolve(import.meta.dirname, '..'), resolve(import.meta.dirname, '../../packages/money')] },
    proxy: { '/api': { ...proxyTarget, ws: true }, '/ws': { ...proxyTarget, ws: true }, '/media': { ...proxyTarget } },
  },
})
