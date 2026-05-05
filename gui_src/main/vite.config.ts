import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: "../../src/focus/GUI/main/",
    assetsDir: ""
  },
  server: {
    proxy: {
      '/api': 'http://localhost:5050'
    }
  }
})
