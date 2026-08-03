import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: "../../src/focus/GUI/main/",
    assetsDir: "",
    // outDir sits outside this project root, so Vite will not empty it on its
    // own. Force it: only the current build's content-hashed assets remain.
    emptyOutDir: true
  },
  server: {
    proxy: {
      '/api': 'http://localhost:5050'
    }
  }
})
