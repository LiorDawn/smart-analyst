import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物输出 dist/（FastAPI 静态托管 frontend/dist）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 本地 dev 时代理到 FastAPI 后端
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
