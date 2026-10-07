/**
 * Theme preference (system | light | dark) and its resolved value.
 *
 * The preference is persisted in localStorage and mirrored on
 * <html data-theme>. The resolved theme is applied as the `.dark` class,
 * which is the only thing the design tokens key on (DESIGN.md 9).
 * Module-level state: every caller shares one instance.
 */
import { readonly, ref } from 'vue';

export type ThemePreference = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'focus-theme';
const LEGACY_KEY = 'focus-theme-override';
const TRANSITION_CLASS = 'theme-transition';
const TRANSITION_MS = 200;

const preference = ref<ThemePreference>('system');
const isDark = ref(false);
let media: MediaQueryList | null = null;

function systemPrefersDark(): boolean {
  return media?.matches ?? false;
}

function apply(animate: boolean) {
  const root = document.documentElement;
  isDark.value = preference.value === 'dark' || (preference.value === 'system' && systemPrefersDark());
  if (animate) {
    root.classList.add(TRANSITION_CLASS);
    window.setTimeout(() => root.classList.remove(TRANSITION_CLASS), TRANSITION_MS);
  }
  root.classList.toggle('dark', isDark.value);
  root.dataset.theme = preference.value;
}

function readStoredPreference(): ThemePreference {
  try {
    // The legacy key only meant "ignore OS changes this session"; it held no theme.
    localStorage.removeItem(LEGACY_KEY);
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch { /* storage unavailable: fall back to system */ }
  return 'system';
}

/** Call once before mounting the app, so the first paint uses the right theme. */
export function initTheme() {
  try {
    media = window.matchMedia('(prefers-color-scheme: dark)');
    media.addEventListener('change', () => {
      if (preference.value === 'system') apply(true);
    });
  } catch { media = null; }
  preference.value = readStoredPreference();
  apply(false);
}

export function useTheme() {
  const setPreference = (value: ThemePreference) => {
    preference.value = value;
    try { localStorage.setItem(STORAGE_KEY, value); } catch { /* not persisted */ }
    apply(true);
  };

  return {
    preference: readonly(preference),
    isDark: readonly(isDark),
    setPreference,
  };
}
