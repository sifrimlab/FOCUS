/**
 * Shared start-up for both GUIs: self-hosted fonts (DESIGN.md 2.1), the theme
 * resolved before the first paint (DESIGN.md 9), Pinia, then mount.
 * Each app imports its own style entry before calling this.
 */
import '@fontsource-variable/inter/opsz.css';
import '@fontsource-variable/jetbrains-mono/wght.css';
import { createApp, type Component } from 'vue';
import { createPinia } from 'pinia';
import { initTheme } from './composables/useTheme';

export function mountFocusApp(root: Component, selector = '#app') {
  initTheme();
  createApp(root).use(createPinia()).mount(selector);
}
