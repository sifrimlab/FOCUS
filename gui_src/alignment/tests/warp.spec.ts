/**
 * Landmark warp maths: parity with the Python implementation (which applies the
 * confirmed warp to every spot) and the behaviour the operator relies on live.
 */
import { describe, it, expect } from 'vitest';
import { mat3 } from 'gl-matrix';
import parity from './fixtures/warp_parity.json';
import {
  buildWarp, leaveOneOut, residualJacobian, foldedCells, insideAny, invertWarp, type Landmark,
} from '../src/utils/warp';
import { projectiveTransformPoint } from '../src/utils/matrix';

interface ParityCase {
  name: string;
  matrix_column_major: number[];
  landmarks: Landmark[];
  smoothing: number;
  query: [number, number][];
  kind: string;
  mapped: [number, number][];
  jacobian_det: number[];
  leave_one_out: (number | null)[];
  anchors?: [number, number][];
}

const close = (a: number, b: number, rtol = 1e-9, atol = 1e-7) =>
  Math.abs(a - b) <= atol + rtol * Math.abs(b);

describe('parity with focus.spatial.warp', () => {
  for (const c of parity as ParityCase[]) {
    it(c.name, () => {
      // Plain doubles: the app's float32 matrices reach Python as the same float32-exact values.
      const m = c.matrix_column_major as unknown as mat3;
      const w = buildWarp(m, c.landmarks, c.smoothing, c.anchors ?? []);
      expect(w.residual.kind).toBe(c.kind);
      c.query.forEach(([x, y], i) => {
        const [mx, my] = w.map(x, y);
        expect(close(mx, c.mapped[i]![0])).toBe(true);
        expect(close(my, c.mapped[i]![1])).toBe(true);
        const [hx, hy] = projectiveTransformPoint(m, x, y);
        const [a, b, cc, d] = residualJacobian(w.residual, hx, hy);
        expect(close(a * d - b * cc, c.jacobian_det[i]!, 1e-9, 1e-9)).toBe(true);
      });
      const loo = leaveOneOut(m, c.landmarks, c.smoothing, c.anchors ?? []);
      loo.forEach((v, i) => {
        const want = c.leave_one_out[i];
        if (want === null || want === undefined) expect(Number.isNaN(v)).toBe(true);
        else expect(close(v, want, 1e-8, 1e-8)).toBe(true);
      });
    });
  }
});

describe('warp behaviour', () => {
  const eye = mat3.create();

  it('pins hold: an unsmoothed spline passes through every landmark', () => {
    const lm: Landmark[] = [
      { src: [0, 0], dst: [5, 3] }, { src: [100, 0], dst: [110, -4] },
      { src: [100, 100], dst: [96, 104] }, { src: [0, 100], dst: [-2, 98] },
      { src: [50, 50], dst: [58, 49] },
    ];
    const w = buildWarp(eye, lm, 0);
    expect(w.nonrigid).toBe(true);
    for (const l of lm) {
      const [x, y] = w.map(l.src[0], l.src[1]);
      expect(Math.hypot(x - l.dst[0], y - l.dst[1])).toBeLessThan(1e-8);
    }
  });

  it('no landmarks leaves the matrix untouched', () => {
    const m = mat3.fromValues(1.1, 0.1, 0, -0.2, 0.9, 0, 30, 40, 1);
    const w = buildWarp(m, []);
    expect(w.nonrigid).toBe(false);
    expect(w.map(10, 20)).toEqual(projectiveTransformPoint(m, 10, 20));
  });

  it('crossed landmarks are reported as folded cells', () => {
    const lm: Landmark[] = [
      { src: [0, 0], dst: [0, 0] }, { src: [1000, 0], dst: [1000, 0] },
      { src: [1000, 1000], dst: [1000, 1000] }, { src: [0, 1000], dst: [0, 1000] },
      { src: [450, 500], dst: [610, 500] }, { src: [550, 500], dst: [390, 500] },
    ];
    const folds = foldedCells(buildWarp(eye, lm, 0), { x: 0, y: 0, width: 1000, height: 1000 }, 20);
    expect(folds.centres.length).toBeGreaterThan(0);
    expect(folds.centres.some(([x, y]) => Math.abs(x - 500) < 60 && Math.abs(y - 500) < 60)).toBe(true);
  });

  it('point in polygon', () => {
    const sq: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    expect(insideAny([sq], 5, 5)).toBe(true);
    expect(insideAny([sq], 15, 5)).toBe(false);
    expect(insideAny([[[0, 0], [1, 1]]], 0.5, 0.5)).toBe(false);
  });
});

describe('warp inverse', () => {
  it('invertWarp undoes map for matrix-only and spline warps', () => {
    const m = mat3.fromValues(1.1, 0.05, 1e-5, -0.1, 0.95, -2e-5, 30, 40, 1);
    const lm: Landmark[] = [
      { src: [0, 0], dst: [35, 38] }, { src: [400, 0], dst: [470, 30] },
      { src: [400, 400], dst: [420, 420] }, { src: [0, 400], dst: [-10, 430] },
      { src: [200, 200], dst: [230, 250] },
    ];
    for (const w of [buildWarp(m, []), buildWarp(m, lm)]) {
      for (const [x, y] of [[10, 20], [200, 210], [390, 5], [123, 321]] as [number, number][]) {
        const [qx, qy] = w.map(x, y);
        const back = invertWarp(w, qx, qy)!;
        expect(back).not.toBeNull();
        expect(Math.hypot(back[0] - x, back[1] - y)).toBeLessThan(1e-4);
      }
    }
  });
});
