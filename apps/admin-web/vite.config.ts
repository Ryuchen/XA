import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, fileURLToPath(new URL('.', import.meta.url)), 'VITE_')
  const apiBase = process.env.VITE_API_BASE || env.VITE_API_BASE || '/api/admin'
  const devApiTarget = process.env.XA_ADMIN_DEV_API_TARGET || 'http://127.0.0.1:8000'
  if (command === 'build') {
    const apiUrl = new URL(apiBase, 'http://localhost')
    if (!/^\/api\/admin\/?$/.test(apiUrl.pathname) || apiUrl.search || apiUrl.hash) {
      throw new Error('管理后台 VITE_API_BASE 必须以 /api/admin 结尾；/api 仅用于陪玩端和小程序')
    }
  }
  return {
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()], dts: command === 'serve' ? 'auto-imports.d.ts' : false }),
    Components({ resolvers: [ElementPlusResolver()], dts: command === 'serve' ? 'components.d.ts' : false }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // 仓库内共享金额包，与 tsconfig paths 对齐，使 `@xa/money` 真实可解析。
      '@xa/money': fileURLToPath(new URL('../../packages/money/src', import.meta.url)),
    },
  },
  server: {
    port: 5180,
    proxy: {
      '/api': {
        target: devApiTarget,
        changeOrigin: true,
      },
      '/media': {
        target: devApiTarget,
        changeOrigin: true,
      },
      '/ws': {
        target: devApiTarget.replace(/^http/, 'ws'),
        ws: true,
        changeOrigin: true,
      },
    },
  },
  }
})
