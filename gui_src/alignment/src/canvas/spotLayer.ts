/**
 * Spot rendering for both layers. Each spot is a rectangle of the spot size
 * (scaled by the shared boost) in its cluster color, filtered by class and
 * by foreground state. Display only: nothing here reaches the export.
 */
import type { Graphics } from 'pixi.js';
import type { mat3 } from 'gl-matrix';
import type { SpotModalityPayload } from '../api/types';
import type { ForegroundMode } from '../store/main';
import { projectiveTransformPoint } from '../utils/matrix';
import { getReferenceColor, getTargetColor } from '../utils/colors';

export interface SpotStyle {
  spotSize: [number, number];
  boost: number;
  classFilter: number[];
  foregroundMode: ForegroundMode;
}

/**
 * Moving layer. Affine transforms are applied by the container; projective
 * ones are applied per spot here (the container then stays at identity).
 */
export function drawTargetSpots(g: Graphics, data: SpotModalityPayload, style: SpotStyle, m: mat3, projective: boolean) {
  const [rx, ry] = style.spotSize;
  const drawRx = rx * style.boost;
  const drawRy = ry * style.boost;
  g.clear();
  for (const spot of data) {
    if (!style.classFilter.includes(spot.class)) continue;
    if (style.foregroundMode === 'foreground' && !spot.foreground) continue;
    if (style.foregroundMode === 'background' && spot.foreground) continue;
    const color = getTargetColor(spot.class);
    const lx = spot.spatial[0]; const ly = spot.spatial[1];
    if (projective) {
      const c0 = projectiveTransformPoint(m, lx - drawRx/2, ly - drawRy/2);
      const c1 = projectiveTransformPoint(m, lx + drawRx/2, ly - drawRy/2);
      const c2 = projectiveTransformPoint(m, lx + drawRx/2, ly + drawRy/2);
      const c3 = projectiveTransformPoint(m, lx - drawRx/2, ly + drawRy/2);
      g.poly([c0[0], c0[1], c1[0], c1[1], c2[0], c2[1], c3[0], c3[1]]);
    } else {
      g.rect(lx - drawRx/2, ly - drawRy/2, drawRx, drawRy);
    }
    g.fill(color);
  }
}

/** Fixed layer, drawn in local coordinates; the container carries the fit. */
export function drawReferenceSpots(g: Graphics, spots: SpotModalityPayload, style: SpotStyle) {
  const [rx, ry] = style.spotSize;
  const drawRx = rx * style.boost;
  const drawRy = ry * style.boost;

  spots.forEach(spot => {
    const isClassVisible = style.classFilter.includes(spot.class);
    let isForegroundVisible = true;
    if (style.foregroundMode === 'foreground') isForegroundVisible = spot.foreground;
    if (style.foregroundMode === 'background') isForegroundVisible = !spot.foreground;
    if (!isClassVisible || !isForegroundVisible) return;

    const color = getReferenceColor(spot.class);
    g.rect(spot.spatial[0] - drawRx / 2, spot.spatial[1] - drawRy / 2, drawRx, drawRy);
    g.fill(color);
  });
}
