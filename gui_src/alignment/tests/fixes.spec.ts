/**
 * Behavior of the alignment fixes: initial fit and first render, reset to the
 * initial fit, resize keeping the layers aligned, rotate drag under pan/zoom,
 * and spot id 0 in the SPOT→IMAGE export.
 */
import { describe, it, expect } from 'vitest';
import { mat3 } from 'gl-matrix';
import { PAIRS } from './harness/fixtures';
import { drag, openSession, settle, targetCornersOnScreen, triggerResize, type Ctx } from './harness/session';
import { apps, renders, screenSize } from './harness/pixiMock';
import { matHex } from './harness/serialize';
import { makeDriver } from './currentDriver';
import { shiftForResize, targetFitMatrix } from '../src/canvas/layerFit';
import { computeExportPayload } from '../src/utils/export';
import { worldToScreen } from '../src/canvas/viewTransform';
import type { Metadata } from '../src/api/types';

const SIZE = { width: 1000, height: 700 };

const mapping = (c: Ctx) => {
  const inv = mat3.invert(mat3.create(), c.store.referenceTransform)!;
  return Array.from(mat3.multiply(mat3.create(), inv, c.store.targetTransform));
};

const expectClose = (a: number[], b: number[], tol: number) =>
  a.forEach((v, i) => expect(Math.abs(v - b[i]!)).toBeLessThanOrEqual(tol * Math.max(1, Math.abs(v))));

async function withSession(pair: (typeof PAIRS)[number], fn: (c: Ctx) => Promise<void>) {
  renders.length = 0;
  const s = await openSession(await makeDriver(), pair, SIZE);
  try { await fn(s.ctx); } finally { s.close(); }
}

describe('each sample starts at its fit and is drawn without interaction', () => {
  for (const pair of PAIRS) {
    it(pair.name, () => withSession(pair, async c => {
      const fit = targetFitMatrix(SIZE.width, SIZE.height, 1, c.store.targetMeta, c.store.targetData);
      expect(matHex(c.store.targetTransform)).toBe(matHex(fit));

      const targetApp = apps.find(a => a.canvas === c.canvas())!;
      const last = renders.filter(r => r.app === targetApp).at(-1);
      expect(last?.drawn ?? 0).toBeGreaterThan(0);
    }));
  }
});

describe('reset transform returns to the initial fit', () => {
  for (const pair of PAIRS) {
    it(pair.name, () => withSession(pair, async c => {
      const fit = matHex(c.store.targetTransform);
      await c.d.flip(c.w, 'h');
      await c.d.type(c.w, 'scale', '1.5');
      await settle();
      const [x, y] = targetCornersOnScreen(c)[0]!;
      await drag(c, [[x, y], [x + 30, y - 20]]);
      await settle();
      expect(matHex(c.store.targetTransform)).not.toBe(fit);

      await c.d.resetTransform(c.w);
      await settle();
      expect(matHex(c.store.targetTransform)).toBe(fit);

      const before = c.store.targetTransform;
      triggerResize({ width: 1100, height: 760 });
      await settle();
      await c.d.resetTransform(c.w);
      await settle();
      expect(matHex(c.store.targetTransform)).toBe(matHex(shiftForResize(before, 100, 60)));
    }));
  }
});

describe('a resize keeps the two layers aligned', () => {
  for (const pair of PAIRS) {
    it(pair.name, () => withSession(pair, async c => {
      await c.d.flip(c.w, 'v');
      await c.d.type(c.w, 'rotation', '20');
      await settle();
      const [x, y] = targetCornersOnScreen(c)[2]!;
      await drag(c, [[x, y], [x - 14, y + 9]]);
      await settle();

      const before = mapping(c);
      triggerResize({ width: 1180, height: 820 });
      await settle();
      expectClose(mapping(c), before, 1e-4);

      triggerResize({ width: 900, height: 640 });
      await settle();
      expectClose(mapping(c), before, 1e-4);
    }));
  }
});

describe('rotate drag follows the pointer when the view is panned and zoomed', () => {
  for (const pair of [PAIRS[0]!, PAIRS[3]!]) {
    it(pair.name, () => withSession(pair, async c => {
      c.store.globalZoom = 1.4;
      c.store.viewOffset = [60, -35];
      await settle();

      const corners = targetCornersOnScreen(c);
      const lc = [0, 1].map(k => corners.reduce((s, p) => s + p[k]!, 0) / 4) as [number, number];
      // Rotation pivot: the layer's local center mapped to world, then to screen.
      const m = c.store.targetTransform;
      const center = c.store.targetMeta!.modality_type === 'IMAGE'
        ? [c.store.targetMeta!.image_shape![1] / 2, c.store.targetMeta!.image_shape![0] / 2]
        : (() => {
            const xs = (c.store.targetData as any[]).map(s => s.spatial[0]);
            const ys = (c.store.targetData as any[]).map(s => s.spatial[1]);
            return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
          })();
      const pivot = worldToScreen(
        { width: screenSize.width, height: screenSize.height, zoom: c.store.globalZoom, offset: c.store.viewOffset },
        center[0]! * m[0] + center[1]! * m[3] + m[6], center[0]! * m[1] + center[1]! * m[4] + m[7],
      );
      expect(Math.hypot(pivot[0] - lc[0], pivot[1] - lc[1])).toBeLessThan(1);

      const k = corners[2]!;
      const dx = k[0] - pivot[0]; const dy = k[1] - pivot[1]; const r = Math.hypot(dx, dy) + 22;
      const a0 = Math.atan2(dy, dx); const theta = 0.35;
      const path = Array.from({ length: 11 }, (_, i) => {
        const a = a0 + (theta * i) / 10;
        return [pivot[0] + r * Math.cos(a), pivot[1] + r * Math.sin(a)] as [number, number];
      });
      const rot = () => Math.atan2(c.store.targetTransform[1], c.store.targetTransform[0]);
      const r0 = rot();
      await drag(c, path);
      await settle();
      const turned = Math.atan2(Math.sin(rot() - r0), Math.cos(rot() - r0));
      expect(Math.abs(turned - theta)).toBeLessThan(1e-3);
    }));
  }
});

describe('SPOT→IMAGE export keeps spot id 0', () => {
  it('covering spot with id 0', () => {
    const refMeta: Metadata = { modality_type: 'SPOT', modality_name: 'ref', spot_size: [40, 40] };
    const tgtMeta: Metadata = { modality_type: 'IMAGE', modality_name: 'img', image_shape: [10, 10] };
    const refSpots = [
      { spatial: [500, 500], class: 0, foreground: true, id: 7 },
      { spatial: [0, 0], class: 0, foreground: true, id: 0 },
    ];
    const payload = computeExportPayload(refMeta, tgtMeta, refSpots, null, mat3.create(), mat3.create());
    expect(payload.pixels[0]).toEqual({ x: 0, y: 0, covering_spot_id: 0 });
  });
});
