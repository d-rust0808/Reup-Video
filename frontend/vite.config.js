import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  server: {
    host: '0.0.0.0',
    port: 6001,
    strictPort: true,
    allowedHosts: true,
    watch: {
      ignored: [
        '**/data/**',
        '**/release/**',
        '**/dist/**',
        '**/electron/**',
        '**/*.sqlite*',
        '**/*.log',
        '**/tmp/**',
      ],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:6000',
        changeOrigin: true,
        ws: true,
      },
      '/ws': {
        target: 'http://127.0.0.1:6000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
