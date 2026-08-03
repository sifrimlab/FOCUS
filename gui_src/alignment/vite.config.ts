import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: "../../src/focus/GUI/alignment/",
    assetsDir: "",
    // outDir sits outside this project root, so Vite will not empty it on its
    // own. Force it: only the current build's content-hashed assets remain.
    emptyOutDir: true
  }
})
