import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'SELF_AGENT_')
  const target = env.SELF_AGENT_API_TARGET || 'http://127.0.0.1:8025'
  const online = !!env.SELF_AGENT_API_TARGET
  return {
    plugins: [vue()],
    base: '/self-agent/',
    server: {
      proxy: {
        '/self-agent/api': {
          target,
          changeOrigin: true,
          rewrite: path => online ? path : path.replace('/self-agent', ''),
        },
      },
    },
  }
})
