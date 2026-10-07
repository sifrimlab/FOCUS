/**
 * Canvas mark colors read from the design tokens (DESIGN.md 8.17), so Pixi
 * draws with the same values as the CSS. Re-read whenever the theme flips.
 */
import { nextTick, shallowRef, watch } from 'vue';
import { useTheme } from '@focus/ui/composables/useTheme';

export interface PixiColor { color: number; alpha: number }
export interface CanvasPalette { handle: PixiColor; handleFill: PixiColor; handleHalo: PixiColor }

/** Resolves a custom property to a concrete color through a hidden probe element. */
function resolveToken(name: string): PixiColor {
  const probe = document.createElement('span');
  probe.style.display = 'none';
  probe.style.color = `var(${name})`;
  document.body.appendChild(probe);
  const value = getComputedStyle(probe).color;
  probe.remove();
  const [r = 0, g = 0, b = 0, a = 1] = (value.match(/[\d.]+/g) ?? []).map(Number);
  return { color: (r << 16) | (g << 8) | b, alpha: a };
}

const read = (): CanvasPalette => ({
  handle: resolveToken('--canvas-handle'),
  handleFill: resolveToken('--canvas-handle-fill'),
  handleHalo: resolveToken('--canvas-handle-halo'),
});

export function useCanvasPalette() {
  const palette = shallowRef<CanvasPalette>(read());
  const { isDark } = useTheme();
  watch(isDark, async () => {
    await nextTick();
    palette.value = read();
  });
  return palette;
}
