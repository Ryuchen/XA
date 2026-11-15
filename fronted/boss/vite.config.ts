import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'
const backend = process.env.XA_API_PROXY || 'http://127.0.0.1:18080'
export default defineConfig({
  base: process.env.XA_BASE_PATH || '/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': resolve(import.meta.dirname, 'src') }, dedupe: ['react', 'react-dom'] },
  server: {
    host: process.env.XA_DEV_HOST || '127.0.0.1', port: 13101, strictPort: true,
    allowedHosts: ['sl.esports.club', 'xa.esports.club'],
    watch: { usePolling: true, interval: 150, awaitWriteFinish: { stabilityThreshold: 200, pollInterval: 50 } },
    fs: { allow: [resolve(import.meta.dirname, '..'), resolve(import.meta.dirname, '../../packages/money')] },
    proxy: { '/api': { target: backend, changeOrigin: true, ws: true }, '/ws': { target: backend, changeOrigin: true, ws: true }, '/media': { target: backend, changeOrigin: true } },
  },
})
