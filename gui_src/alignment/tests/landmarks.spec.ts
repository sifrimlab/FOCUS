/**
 * Landmark and exclusion tools of a spot moving layer: pinning, dragging,
 * removing, undo, lassoing exclusions, the fold guard on confirm, the
 * confirm payload, and restoring a warp confirmed in an earlier run.
 */
import { describe, it, expect, vi } from 'vitest';
import { PAIRS } from './harness/fixtures';
import { drag, openSession, settle, type Ctx } from './harness/session';
import { backend } from './harness/apiMock';
import { click } from './harness/driver';
import { makeDriver } from './currentDriver';
import { worldToScreen } from '../src/canvas/viewTransform';
import { movingToWorld, worldToFixed } from '../src/canvas/warpView';
import { screenToWorld } from '../src/canvas/viewTransform';
import { SETTLE_MS } from '../src/canvas/useTransformHistory';
import type { Pt } from '../src/utils/warp';

const SIZE = { width: 1000, height: 700 };
const IMAGE_SPOT = PAIRS.find(p => p.name === 'image-spot')!;
const SPOT_IMAGE = PAIRS.find(p => p.name === 'spot-image')!;

async function withSession(pair: (typeof PAIRS)[number], fn: (c: Ctx) => Promise<void>, initialState?: unknown) {
  const s = await openSession(await makeDriver(), pair, SIZE, { initialState });
  try { await fn(s.ctx); } finally { s.close(); }
}

const view = (c: Ctx) => ({ ...SIZE, zoom: c.store.globalZoom, offset: c.store.viewOffset });

/** Screen position of a moving-layer native point under the current warp. */
const screenOf = (c: Ctx, x: number, y: number): Pt => {
  const [wx, wy] = movingToWorld(c.store, x, y);
  return worldToScreen(view(c), wx, wy);
};

/** Fixed display coordinates under a screen point. */
const fixedAt = (c: Ctx, [sx, sy]: Pt): Pt => {
  const [wx, wy] = screenToWorld(view(c), sx, sy);
  return worldToFixed(c.store, wx, wy);
};

const setTool = async (c: Ctx, label: 'Landmarks' | 'Exclude') => {
  click(document.querySelector(`[role=radio][aria-label^="${label}"]`));
  await settle();
};

/** Press at a moving-layer point and drag by (dx, dy) screen pixels. */
const pin = async (c: Ctx, x: number, y: number, dx = 0, dy = 0) => {
  const [sx, sy] = screenOf(c, x, y);
  await drag(c, dx || dy ? [[sx, sy], [sx + dx / 2, sy + dy / 2], [sx + dx, sy + dy]] : [[sx, sy]]);
  await settle();
};

const near = (a: Pt, b: Pt, tol: number) => Math.hypot(a[0] - b[0], a[1] - b[1]) <= tol;

describe('landmark tool', () => {
  it('is offered only for a spot moving layer', async () => {
    await withSession(SPOT_IMAGE, async () => {
      expect(document.querySelector('[role=radio][aria-label^="Landmarks"]')).toBeNull();
    });
    await withSession(IMAGE_SPOT, async () => {
      expect(document.querySelector('[role=radio][aria-label^="Landmarks"]')).not.toBeNull();
    });
  });

  it('a press pins the structure under the cursor and the drag pins it where it is dropped', () =>
    withSession(IMAGE_SPOT, async c => {
      await setTool(c, 'Landmarks');
      const start = screenOf(c, 45, 43);
      await pin(c, 45, 43, 30, -12);
      expect(c.store.landmarks).toHaveLength(1);
      const l = c.store.landmarks[0]!;
      expect(near(l.src, [45, 43], 1e-3)).toBe(true);
      expect(near(l.dst, fixedAt(c, [start[0] + 30, start[1] - 12]), 1e-6)).toBe(true);
      // The pinned point lands under the drop point (the default stiffness gives way by far
      // less than a pixel).
      expect(near(screenOf(c, 45, 43), [start[0] + 30, start[1] - 12], 0.5)).toBe(true);
    }));

  it('dragging a pin bends the tissue around it; the layer outline stays put', () =>
    withSession(IMAGE_SPOT, async c => {
      await setTool(c, 'Landmarks');
      const corners = c.store.borderAnchors.slice(0, 4);
      expect(corners).toHaveLength(4);
      const before = corners.map(([x, y]) => screenOf(c, x, y));
      const farBefore = screenOf(c, 145, 123);
      await pin(c, 45, 43, 30, -12);
      corners.forEach(([x, y], i) => expect(near(screenOf(c, x, y), before[i]!, 0.05)).toBe(true));
      // Tissue away from the pin barely moves; the pinned point moved by the whole drag.
      expect(near(screenOf(c, 145, 123), farBefore, 3)).toBe(true);
    }));

  it('a click without a drag anchors the point in place; right-click removes a pin', () =>
    withSession(IMAGE_SPOT, async c => {
      await setTool(c, 'Landmarks');
      const before = screenOf(c, 105, 83);
      await pin(c, 105, 83);
      expect(c.store.landmarks).toHaveLength(1);
      expect(near(screenOf(c, 105, 83), before, 1e-3)).toBe(true);
      const [sx, sy] = screenOf(c, 105, 83);
      c.canvas().dispatchEvent(new MouseEvent('contextmenu', { clientX: sx, clientY: sy, bubbles: true, cancelable: true }));
      await settle();
      expect(c.store.landmarks).toHaveLength(0);
    }));

  it('pressing an existing pin moves it instead of adding one', () =>
    withSession(IMAGE_SPOT, async c => {
      await setTool(c, 'Landmarks');
      await pin(c, 65, 63, 20, 0);
      const [sx, sy] = screenOf(c, 65, 63);
      await drag(c, [[sx, sy], [sx, sy + 15]]);
      await settle();
      expect(c.store.landmarks).toHaveLength(1);
      expect(near(screenOf(c, 65, 63), [sx, sy + 15], 0.5)).toBe(true);
    }));

  it('undo removes the last pin (each pin is one change)', () =>
    withSession(IMAGE_SPOT, async c => {
      await setTool(c, 'Landmarks');
      await pin(c, 45, 43, 10, 10);
      await vi.advanceTimersByTimeAsync(SETTLE_MS + 10);
      await pin(c, 125, 103, -10, 5);
      await vi.advanceTimersByTimeAsync(SETTLE_MS + 10);
      expect(c.store.landmarks).toHaveLength(2);
      c.store.pendingCommand = { type: 'undo' };
      await settle();
      expect(c.store.landmarks).toHaveLength(1);
      c.store.pendingCommand = { type: 'redo' };
      await settle();
      expect(c.store.landmarks).toHaveLength(2);
    }));

  it('three or more pins bend the layer, and confirm posts the pins with the matrix', () =>
    withSession(IMAGE_SPOT, async c => {
      await setTool(c, 'Landmarks');
      await pin(c, 5, 3);
      await pin(c, 165, 3);
      await pin(c, 5, 143);
      await pin(c, 165, 143);
      await pin(c, 85, 63, 25, 0);
      expect(c.store.warp.nonrigid).toBe(true);
      expect(c.store.landmarkErrors.every(Number.isFinite)).toBe(true);
      await c.d.confirm(c.w);
      await settle();
      const sent = backend.confirmed[0] as Record<string, any>;
      expect(sent.transform_matrix).toHaveLength(9);
      expect(sent.landmarks.src).toHaveLength(5);
      expect(sent.landmarks.dst).toHaveLength(5);
      expect(sent.smoothing).toBe(c.store.smoothing);
      expect(sent.exclusions).toBeUndefined();
    }));
});

describe('exclusion tool', () => {
  it('a lasso stores a polygon in the moving layer frame and it is exported', () =>
    withSession(IMAGE_SPOT, async c => {
      await setTool(c, 'Exclude');
      const corners: Pt[] = [[40, 40], [100, 40], [100, 100], [40, 100]];
      const screen = corners.map(([x, y]) => screenOf(c, x, y));
      // Densify the path so every segment is longer than the lasso step.
      const path: Pt[] = [];
      screen.forEach((p, i) => {
        const q = screen[(i + 1) % screen.length]!;
        for (let t = 0; t < 1; t += 0.25) path.push([p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]);
      });
      await drag(c, path);
      await settle();
      expect(c.store.exclusions.moving).toHaveLength(1);
      const poly = c.store.exclusions.moving[0]!;
      expect(near(poly[0]!, [40, 40], 1e-3)).toBe(true);
      await c.d.confirm(c.w);
      await settle();
      const sent = backend.confirmed[0] as Record<string, any>;
      expect(sent.exclusions.moving).toHaveLength(1);
      expect(sent.landmarks).toBeUndefined();
    }));
});

describe('fold guard', () => {
  it('a folding warp is confirmed only on a second click', () =>
    withSession(IMAGE_SPOT, async c => {
      const w = c.store.warp;
      const at = (x: number, y: number) => w.map(x, y);
      // Anchor the corners, then swap two neighbouring pins across each other.
      const a = at(70, 63), b = at(100, 63);
      c.store.landmarks = [
        { src: [5, 3], dst: at(5, 3) }, { src: [165, 3], dst: at(165, 3) },
        { src: [5, 143], dst: at(5, 143) }, { src: [165, 143], dst: at(165, 143) },
        { src: [70, 63], dst: [b[0] + 10, b[1]] }, { src: [100, 63], dst: [a[0] - 10, a[1]] },
      ];
      c.store.smoothing = 0;
      await settle();
      expect(c.store.foldCount).toBeGreaterThan(0);
      await c.d.confirm(c.w);
      await settle();
      expect(backend.confirmed).toHaveLength(0);
      expect(c.store.confirmArmed).toBe(true);
      click([...document.querySelectorAll('button')].find(b => b.textContent?.includes('Confirm despite folds')));
      await settle();
      expect(backend.confirmed).toHaveLength(1);
    }));
});

describe('stored warp', () => {
  it('a warp confirmed earlier is restored with its pins and exclusions', async () => {
    const matrix = [1.1, 0.05, 0, -0.04, 0.9, 0, 30, 20, 1];
    const state = {
      matrix,
      landmarks: { src: [[5, 3], [165, 3], [85, 143]], dst: [[40, 25], [215, 35], [130, 150]] },
      smoothing: 0.02,
      exclusions: { moving: [[[40, 40], [60, 40], [60, 60]]], fixed: [] },
    };
    await withSession(IMAGE_SPOT, async c => {
      expect(c.store.landmarks).toHaveLength(3);
      expect(c.store.landmarks[1]!.dst).toEqual([215, 35]);
      expect(c.store.smoothing).toBe(0.02);
      expect(c.store.exclusions.moving).toHaveLength(1);
      const got = Array.from(c.store.mapMatrix);
      got.forEach((v, i) => expect(Math.abs(v - matrix[i]!)).toBeLessThan(1e-5));
      // Restoring is the starting point, not an undoable change.
      await vi.advanceTimersByTimeAsync(SETTLE_MS + 10);
      c.store.pendingCommand = { type: 'undo' };
      await settle();
      expect(c.store.landmarks).toHaveLength(3);
    }, state);
  });
});
