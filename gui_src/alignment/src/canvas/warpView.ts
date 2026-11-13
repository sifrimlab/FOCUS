/**
 * Frames of the landmark warp on screen. Three frames meet here:
 *   moving native  --W-->  fixed display  --M_fixed-->  world  --view-->  screen
 * W is the pair matrix refined by the landmarks (utils/warp.ts); M_fixed is the
 * fixed layer's fit (store.referenceTransform). Display only: the export sends
 * the matrix and the landmarks, and the backend applies the same maths to every spot.
 */
import { mat3 } from 'gl-matrix';
import type { useMainStore } from '../store/main';
import { projectiveTransformPoint } from '../utils/matrix';
import { applyResidual, invertWarp, type Pt } from '../utils/warp';

type Store = ReturnType<typeof useMainStore>;

/**
 * Per-spot mapping of footprint corners into world coordinates. `forSpot` is
 * called once per spot centre and returns the corner mapper for that spot.
 */
export interface PointMap {
  key: string;
  forSpot(cx: number, cy: number): (px: number, py: number) => Pt;
}

/** One projective matrix applied to every corner (the distort tool). */
export function matrixPointMap(m: mat3): PointMap {
  const map = (px: number, py: number) => projectiveTransformPoint(m, px, py);
  return { key: JSON.stringify(Array.from(m)), forSpot: () => map };
}

/**
 * The landmark warp. The spot centre goes through the full warp; its corners
 * follow the pair matrix plus the centre's residual displacement, so each
 * footprint costs one spline evaluation and keeps its shape under the matrix.
 */
export function warpPointMap(store: Store): PointMap {
  const warp = store.warp;
  const H = warp.matrix;
  const moving = store.targetTransform;
  const f = store.referenceTransform;
  const key = JSON.stringify([
    Array.from(moving), Array.from(f), store.landmarks, store.smoothing, store.borderAnchors,
  ]);
  return {
    key,
    forSpot(cx, cy) {
      const [hx, hy] = projectiveTransformPoint(H, cx, cy);
      const [rx, ry] = applyResidual(warp.residual, hx, hy);
      const dx = rx - hx, dy = ry - hy;
      // Displacement in fixed display units -> world units (linear part of M_fixed).
      const wx = f[0] * dx + f[3] * dy;
      const wy = f[1] * dx + f[4] * dy;
      return (px, py) => {
        const [X, Y] = projectiveTransformPoint(moving, px, py);
        return [X + wx, Y + wy];
      };
    },
  };
}

/** Fixed-layer display coordinates -> world. */
export function fixedToWorld(store: Store, x: number, y: number): Pt {
  return projectiveTransformPoint(store.referenceTransform, x, y);
}

/** World -> fixed-layer display coordinates. */
export function worldToFixed(store: Store, wx: number, wy: number): Pt {
  const inv = mat3.invert(mat3.create(), store.referenceTransform);
  return inv ? projectiveTransformPoint(inv, wx, wy) : [wx, wy];
}

/** Moving-native coordinates -> world, through the warp. */
export function movingToWorld(store: Store, x: number, y: number): Pt {
  const [fx, fy] = store.warp.map(x, y);
  return fixedToWorld(store, fx, fy);
}

/** World -> moving-native coordinates (inverse warp); null inside a fold. */
export function worldToMoving(store: Store, wx: number, wy: number): Pt | null {
  const [fx, fy] = worldToFixed(store, wx, wy);
  return invertWarp(store.warp, fx, fy);
}
