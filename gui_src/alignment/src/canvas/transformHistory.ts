/**
 * Undo history of the moving layer: the sample's starting state plus up to
 * HISTORY_CHANGES later states, and a cursor. Snapshots are copied in and
 * out, so restoring one is an exact byte copy, never a recomputation.
 */
import { mat3 } from 'gl-matrix';
import type { Exclusions, Landmark, Pt } from '../utils/warp';

export const HISTORY_CHANGES = 10;

export interface TransformSnapshot {
  transform: mat3;
  /** Affine transform saved before the current distortion ("Reset distortion" target). */
  distortBefore: mat3 | null;
  /** Landmark warp state; layer-native coordinates, so a resize never rewrites it. */
  landmarks?: Landmark[];
  smoothing?: number;
  exclusions?: Exclusions;
}

// Plain copies (store state arrives as reactive proxies, which structuredClone rejects).
const copyPolys = (polys: Pt[][]): Pt[][] => polys.map(poly => poly.map(([x, y]) => [x, y] as Pt));

const copy = (s: TransformSnapshot): TransformSnapshot => ({
  transform: mat3.clone(s.transform),
  distortBefore: s.distortBefore ? mat3.clone(s.distortBefore) : null,
  landmarks: s.landmarks?.map(l => ({ src: [l.src[0], l.src[1]] as Pt, dst: [l.dst[0], l.dst[1]] as Pt })),
  smoothing: s.smoothing,
  exclusions: s.exclusions
    ? { moving: copyPolys(s.exclusions.moving), fixed: copyPolys(s.exclusions.fixed) }
    : undefined,
});

export function createTransformHistory(capacity = HISTORY_CHANGES) {
  let states: TransformSnapshot[] = [];
  let cursor = -1;

  return {
    /** Starts over from one state (a new sample). */
    reset(s: TransformSnapshot) {
      states = [copy(s)];
      cursor = 0;
    },
    /** Records a change: drops the redo branch, then the oldest state beyond capacity. */
    push(s: TransformSnapshot) {
      states = states.slice(0, cursor + 1);
      states.push(copy(s));
      if (states.length > capacity + 1) states.shift();
      cursor = states.length - 1;
    },
    undo(): TransformSnapshot | null {
      if (cursor <= 0) return null;
      cursor -= 1;
      return copy(states[cursor]!);
    },
    redo(): TransformSnapshot | null {
      if (cursor < 0 || cursor >= states.length - 1) return null;
      cursor += 1;
      return copy(states[cursor]!);
    },
    /** The state the layer is in according to the history. */
    current(): TransformSnapshot | null {
      return cursor >= 0 ? copy(states[cursor]!) : null;
    },
    /** Rewrites every state (a window resize moves all of them together). */
    map(fn: (m: mat3) => mat3) {
      states = states.map(s => ({
        ...s,
        transform: fn(s.transform),
        distortBefore: s.distortBefore ? fn(s.distortBefore) : null,
      }));
    },
    get canUndo() { return cursor > 0; },
    get canRedo() { return cursor >= 0 && cursor < states.length - 1; },
  };
}

export type TransformHistory = ReturnType<typeof createTransformHistory>;
