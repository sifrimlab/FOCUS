/** The scripted session every golden test runs, for every modality pair and canvas size. */
import { vi } from 'vitest';
import { drag, targetCornersOnScreen, triggerResize, wheel, type Ctx, type Step } from './harness/session';

const isSpot = (c: Ctx, layer: 'target' | 'reference') =>
  (layer === 'target' ? c.store.targetMeta : c.store.referenceMeta)?.modality_type === 'SPOT';
const hasFg = (c: Ctx, layer: 'target' | 'reference') =>
  isSpot(c, layer) && !(layer === 'target' ? c.store.targetMeta : c.store.referenceMeta)?.foreground_only;
const center = (pts: [number, number][]): [number, number] =>
  [pts.reduce((a, p) => a + p[0], 0) / 4, pts.reduce((a, p) => a + p[1], 0) / 4];

export const SCRIPT: Step[] = [
  { name: 'flip-h', run: c => c.d.flip(c.w, 'h') },
  { name: 'flip-v', run: c => c.d.flip(c.w, 'v') },
  { name: 'type-scale', run: async c => { await c.d.type(c.w, 'scale', '1'); await c.d.type(c.w, 'scale', '1.5'); } },
  { name: 'type-rotation', run: async c => { await c.d.type(c.w, 'rotation', '30'); await c.d.type(c.w, 'rotation', '-12.5'); } },
  { name: 'hold-scale+', run: c => c.d.hold(c.w, 'scale', '+', 350) },
  { name: 'hold-scale-', run: c => c.d.hold(c.w, 'scale', '-', 120) },
  { name: 'hold-rotation-', run: c => c.d.hold(c.w, 'rotation', '-', 250) },
  { name: 'hold-rotation+', run: c => c.d.hold(c.w, 'rotation', '+', 40) },
  { name: 'reset-scale', run: c => c.d.resetField(c.w, 'scale') },
  { name: 'reset-rotation', run: c => c.d.resetField(c.w, 'rotation') },
  { name: 'wheel-aligner', run: async c => { wheel(c, 310, 220, -100); wheel(c, 310, 220, -100); wheel(c, 311, 219, -3); wheel(c, 500, 400, 100); } },
  { name: 'mode-camera', run: c => c.d.setMode(c.w, 'camera') },
  { name: 'pan', run: c => drag(c, [[100, 100], [160, 130], [170, 90]]) },
  { name: 'wheel-camera', run: async c => { wheel(c, 400, 300, -100); wheel(c, 400, 300, -100); } },
  { name: 'hold-zoom+', run: c => c.d.hold(c.w, 'zoom', '+', 300) },
  { name: 'hold-zoom-', run: c => c.d.hold(c.w, 'zoom', '-', 100) },
  { name: 'type-zoom', run: c => c.d.type(c.w, 'zoom', '1.3') },
  { name: 'mode-aligner', run: c => c.d.setMode(c.w, 'aligner') },
  { name: 'drag-frame', run: async c => { const [x, y] = center(targetCornersOnScreen(c)); await drag(c, [[x, y], [x + 20, y + 10], [x + 40, y + 25]]); } },
  { name: 'drag-corner', run: async c => { const [x, y] = targetCornersOnScreen(c)[0]!; await drag(c, [[x, y], [x + 12, y - 8], [x + 30, y - 20]]); } },
  { name: 'drag-edge', run: async c => {
    const p = targetCornersOnScreen(c);
    const x = (p[1]![0] + p[2]![0]) / 2; const y = (p[1]![1] + p[2]![1]) / 2;
    await drag(c, [[x, y], [x + 15, y + 5]]);
  } },
  { name: 'drag-rotate', run: async c => {
    const p = targetCornersOnScreen(c); const ctr = center(p); const k = p[2]!;
    const dx = k[0] - ctr[0]; const dy = k[1] - ctr[1]; const n = Math.hypot(dx, dy);
    const sx = k[0] + (dx / n) * 22; const sy = k[1] + (dy / n) * 22;
    await drag(c, [[sx, sy], [sx - 10, sy + 14], [sx - 25, sy + 24]]);
  } },
  { name: 'reset-distortion', run: c => c.d.resetDistortion(c.w) },
  { name: 'drag-corner-2', run: async c => { const [x, y] = targetCornersOnScreen(c)[3]!; await drag(c, [[x, y], [x - 10, y + 18]]); } },
  { name: 'reset-transform', run: c => c.d.resetTransform(c.w) },
  { name: 'type-scale-after-reset', run: c => c.d.type(c.w, 'scale', '0.75') },
  { name: 'drag-frame-2', run: async c => { const [x, y] = center(targetCornersOnScreen(c)); await drag(c, [[x, y], [x - 33, y + 41]]); } },
  { name: 'opacity', run: c => c.d.setOpacity(c.w, '0.35') },
  { name: 'tgt-classes', when: c => isSpot(c, 'target'), run: async c => {
    await c.d.toggleClass(c.w, 'target', 1); await c.d.noClasses(c.w, 'target');
    await c.d.toggleClass(c.w, 'target', 0); await c.d.toggleClass(c.w, 'target', 1);
    await c.d.toggleClass(c.w, 'target', 0); await c.d.allClasses(c.w, 'target');
    await c.d.toggleClass(c.w, 'target', 1);
  } },
  { name: 'tgt-fg', when: c => hasFg(c, 'target'), run: async c => {
    await c.d.foreground(c.w, 'target', 'foreground'); await c.d.foreground(c.w, 'target', 'background');
  } },
  { name: 'tgt-spot-size', when: c => isSpot(c, 'target'), run: c => c.d.typeSpotSize(c.w, 'target', 0, '14') },
  { name: 'ref-classes', when: c => isSpot(c, 'reference'), run: async c => {
    await c.d.toggleClass(c.w, 'reference', 0); await c.d.toggleClass(c.w, 'reference', 2);
  } },
  { name: 'ref-fg', when: c => hasFg(c, 'reference'), run: c => c.d.foreground(c.w, 'reference', 'foreground') },
  { name: 'ref-spot-size', when: c => isSpot(c, 'reference'), run: c => c.d.typeSpotSize(c.w, 'reference', 1, '60') },
  { name: 'resize', run: async () => { triggerResize({ width: 1100, height: 760 }); await vi.advanceTimersByTimeAsync(0); } },
  { name: 'wheel-after-resize', run: async c => { wheel(c, 640, 380, -100); } },
  { name: 'reset-zoom', run: c => c.d.resetField(c.w, 'zoom') },
  { name: 'confirm', run: c => c.d.confirm(c.w) },
];

export const SIZES = [{ width: 1000, height: 700 }, { width: 997, height: 613 }];

/** Per-sample steps of the app-level session (three samples, confirm each). */
export const APP_SCRIPT: Step[] = [
  { name: 'flip-h', run: c => c.d.flip(c.w, 'h') },
  { name: 'type-rotation', run: c => c.d.type(c.w, 'rotation', '15') },
  { name: 'wheel', run: async c => { wheel(c, 420, 260, -100); } },
  { name: 'drag-frame', run: async c => { const [x, y] = center(targetCornersOnScreen(c)); await drag(c, [[x, y], [x + 25, y - 15]]); } },
  { name: 'drag-corner', run: async c => { const [x, y] = targetCornersOnScreen(c)[1]!; await drag(c, [[x, y], [x + 9, y + 21]]); } },
  { name: 'confirm', run: c => c.d.confirm(c.w) },
];

/**
 * Steps whose results were changed on purpose by the alignment fixes
 * (reset to the initial fit, resize keeping layers together, rotate-drag
 * screen mapping). Everything else must stay byte-identical.
 */
const CHANGED_ON_PURPOSE = new Set(['reset-transform', 'type-scale-after-reset', 'drag-frame-2', 'resize', 'wheel-after-resize', 'drag-rotate']);
export const INVARIANT_SCRIPT: Step[] = SCRIPT.filter(s => !CHANGED_ON_PURPOSE.has(s.name));
