/**
 * Runs one scripted alignment session against the mounted UI and records, after
 * every step, the exact transforms and the store state the UI wrote.
 */
import { flushPromises, type VueWrapper } from '@vue/test-utils';
import { vi } from 'vitest';
import { watch } from 'vue';
import { createPinia, setActivePinia, type Pinia } from 'pinia';
import { useMainStore } from '../../src/store/main';
import { createIdentity, projectiveTransformPoint } from '../../src/utils/matrix';
import { backend } from './apiMock';
import { apps, screenSize } from './pixiMock';
import { resizeCallbacks } from './setup';
import { matHex, exactJson } from './serialize';
import type { Driver } from './driver';
import type { Side } from './fixtures';

export interface Ctx {
  w: VueWrapper;
  d: Driver;
  store: ReturnType<typeof useMainStore>;
  canvas: () => HTMLCanvasElement;
}
export interface Step { name: string; run: (c: Ctx) => Promise<void>; when?: (c: Ctx) => boolean }

export interface SessionOptions {
  /**
   * Overwrite the moving layer's starting transform with identity after it is
   * placed. Reproduces the start of the GUI before the initial-fit fix, so the
   * invariant goldens can compare every other operation byte for byte.
   */
  startAtIdentity?: boolean;
}

const startAt = async (store: ReturnType<typeof useMainStore>, opts: SessionOptions) => {
  if (!opts.startAtIdentity) return;
  store.updateTargetTransform(createIdentity());
  await settle();
};

export const settle = async () => {
  await flushPromises();
  await vi.advanceTimersByTimeAsync(20);
  await flushPromises();
};

export interface OpenSession { ctx: Ctx; close: () => void }

/** Loads one sample, mounts the workspace and lets it settle (the real order: load, then mount). */
export async function openSession(
  makeDriver: (pinia: Pinia) => Driver,
  pair: { ref: Side; tgt: Side },
  size: { width: number; height: number },
  opts: SessionOptions = {},
): Promise<OpenSession> {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame'] });
  Object.assign(screenSize, size);
  resizeCallbacks.length = 0;
  backend.reset();
  backend.enqueue(pair.ref, pair.tgt);

  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useMainStore();
  await store.fetchNextSample();

  const d = makeDriver(pinia);
  const w = d.mount();
  const ctx: Ctx = { w, d, store, canvas: () => d.targetCanvas(w) };

  await settle();
  await vi.advanceTimersByTimeAsync(120);
  await settle();
  await startAt(store, opts);

  return { ctx, close: () => { w.unmount(); vi.useRealTimers(); } };
}

export async function runSession(
  makeDriver: (pinia: Pinia) => Driver,
  pair: { ref: Side; tgt: Side },
  size: { width: number; height: number },
  steps: Step[],
  opts: SessionOptions = {},
): Promise<string> {
  const { ctx, close } = await openSession(makeDriver, pair, size, opts);
  const { store } = ctx;
  const commands: unknown[] = [];
  watch(() => store.pendingCommand, c => { if (c) commands.push(JSON.parse(JSON.stringify(c))); }, { flush: 'sync' });

  const lines: string[] = [];
  const record = (name: string) => {
    lines.push(exactJson({
      step: name,
      commands: commands.splice(0),
      target: matHex(store.targetTransform),
      reference: matHex(store.referenceTransform),
      zoom: store.globalZoom,
      viewOffset: store.viewOffset,
      mode: store.controlMode,
      opacity: store.targetOpacity,
      tgtFilter: store.targetClassFilter,
      refFilter: store.referenceClassFilter,
      tgtFg: store.targetForegroundMode,
      refFg: store.referenceForegroundMode,
      tgtSpot: store.targetSpotSize,
      refSpot: store.referenceSpotSize,
    }));
  };
  record('initial');
  for (const s of steps) {
    if (s.when && !s.when(ctx)) continue;
    await s.run(ctx);
    await settle();
    record(s.name);
  }
  lines.push(exactJson({ confirmed: backend.confirmed }));
  close();
  return lines.join('\n') + '\n';
}

/* ---- Pointer helpers (read-only geometry, used to aim at handles) ---- */

export function targetCornersOnScreen(c: Ctx): [number, number][] {
  const { store } = c;
  const m = store.targetTransform;
  let box: [number, number, number, number];
  if (store.targetMeta!.modality_type === 'IMAGE') {
    const [h, w] = store.targetMeta!.image_shape!;
    box = [0, 0, w, h];
  } else {
    const spots = store.targetData as any[];
    const [rx, ry] = store.targetSpotSize;
    const xs = spots.map(s => s.spatial[0]); const ys = spots.map(s => s.spatial[1]);
    box = [Math.min(...xs) - rx / 2, Math.min(...ys) - ry / 2, Math.max(...xs) + rx / 2, Math.max(...ys) + ry / 2];
  }
  const local: [number, number][] = [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]];
  const { width, height } = screenSize;
  const cx = width / 2; const cy = height / 2;
  return local.map(([x, y]) => {
    const [wx, wy] = projectiveTransformPoint(m, x, y);
    return [(wx - (cx - store.viewOffset[0])) * store.globalZoom + cx, (wy - (cy - store.viewOffset[1])) * store.globalZoom + cy];
  });
}

export async function drag(c: Ctx, path: [number, number][]) {
  const [x0, y0] = path[0]!;
  c.canvas().dispatchEvent(new MouseEvent('mousedown', { clientX: x0, clientY: y0, bubbles: true }));
  for (const [x, y] of path.slice(1)) {
    window.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y, bubbles: true }));
    await vi.advanceTimersByTimeAsync(17);
  }
  window.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
}

export function wheel(c: Ctx, x: number, y: number, deltaY: number) {
  c.canvas().dispatchEvent(new WheelEvent('wheel', { clientX: x, clientY: y, deltaY, cancelable: true, bubbles: true }));
}

export function triggerResize(size: { width: number; height: number }) {
  Object.assign(screenSize, size);
  // Pixi's resizeTo follows the window; the observers then react.
  apps.forEach(a => a.resize());
  [...resizeCallbacks].forEach(cb => cb());
}

/**
 * App-level session: the shell loads each queued sample itself, swaps between
 * the loading screen and the workspace, and moves on after every confirm.
 * Records each sample's starting transforms and every confirmed payload.
 */
export async function runAppSession(
  makeDriver: (pinia: Pinia) => Driver,
  pairs: Array<{ ref: Side; tgt: Side }>,
  size: { width: number; height: number },
  perSample: Step[],
  opts: SessionOptions = {},
): Promise<string> {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame'] });
  Object.assign(screenSize, size);
  resizeCallbacks.length = 0;
  backend.reset();
  pairs.forEach(p => backend.enqueue(p.ref, p.tgt));

  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useMainStore();
  const d = makeDriver(pinia);
  const w = d.mountApp();
  const ctx: Ctx = { w, d, store, canvas: () => d.targetCanvas(w) };
  const lines: string[] = [];
  const snap = (name: string) => lines.push(exactJson({
    step: name, finished: store.isFinished, loading: store.isLoading,
    target: matHex(store.targetTransform), reference: matHex(store.referenceTransform),
    canvases: w.element.parentElement?.querySelectorAll('canvas').length ?? 0,
  }));

  for (let i = 0; i < pairs.length; i++) {
    await settle();
    await vi.advanceTimersByTimeAsync(120);
    await settle();
    await startAt(store, opts);
    snap(`sample-${i}-initial`);
    for (const s of perSample) {
      if (s.when && !s.when(ctx)) continue;
      await s.run(ctx);
      await settle();
      snap(`sample-${i}-${s.name}`);
    }
  }
  await settle();
  snap('end');
  lines.push(exactJson({ confirmed: backend.confirmed }));
  w.unmount();
  vi.useRealTimers();
  return lines.join('\n') + '\n';
}
