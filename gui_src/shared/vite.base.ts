/** Vite settings shared by both GUIs; each app passes only what differs. */
import { defineConfig, type ServerOptions } from 'vite';
import vue from '@vitejs/plugin-vue';

export function focusViteConfig(opts: { outDir: string; proxy?: ServerOptions['proxy'] }) {
  return defineConfig({
    plugins: [vue()],
    base: './',
    build: {
      outDir: opts.outDir,
      assetsDir: '',
      // outDir sits outside the project root, so Vite will not empty it on its
      // own. Force it: only the current build's content-hashed assets remain.
      emptyOutDir: true,
    },
    server: {
      // The shared package and design-tokens.css live one level up.
      fs: { allow: ['..'] },
      proxy: opts.proxy,
    },
  });
}
