/**
 * Initial placement of each layer on the canvas. These matrices feed the
 * exported alignment (the reference one is inverted in utils/export.ts),
 * so the arithmetic is kept exactly as it was in the original canvases.
 */
import type { Metadata, SpotModalityPayload } from '../api/types';
import { mat3 } from 'gl-matrix';
import { createIdentity, multiply, scale, translate } from '../utils/matrix';

/** Target (moving layer) fit: image centered unscaled; spot cloud centered with a Y flip. */
export function targetFitMatrix(
  width: number,
  height: number,
  globalZoom: number,
  meta: Metadata | null,
  data: unknown,
) {
  // Use Base Dimensions
  width = width / globalZoom;
  height = height / globalZoom;

  const m = createIdentity();
  if (!data || !meta) return m;

  if (meta.modality_type === 'IMAGE') {
    if (meta.image_shape) {
        const [h, w] = meta.image_shape;
        const s = 1.0;
        const dx = (width - w * s) / 2;
        const dy = (height - h * s) / 2;
        translate(m, m, [dx, dy]);
        scale(m, m, [s, s]);
    }
  } else {
    const spots = data as SpotModalityPayload;
    const spotSize = meta.spot_size!;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const s of spots) {
      minX = Math.min(minX, s.spatial[0]);
      maxX = Math.max(maxX, s.spatial[0]);
      minY = Math.min(minY, s.spatial[1]);
      maxY = Math.max(maxY, s.spatial[1]);
    }
    const rx = spotSize[0];
    const ry = spotSize[1];
    minX -= rx/2; maxX += rx/2;
    minY -= ry/2; maxY += ry/2;

    const s = 1.0;

    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;

    translate(m, m, [width/2, height/2]);
    scale(m, m, [s, -s]);
    translate(m, m, [-cx, -cy]);
  }
  return m;
}

/**
 * Reference (fixed layer) fit for spot data: centers the spot cloud at the
 * screen center with a Y flip, mirroring targetFitMatrix.
 */
export function referenceSpotFitMatrix(
  width: number,
  height: number,
  meta: Metadata | null,
  data: unknown,
  spotSize: [number, number],
) {
    const m = createIdentity();
    if (!data || !meta) return m;
    if (meta.modality_type !== 'SPOT') return m;

    const spots = data as SpotModalityPayload;
    if (!spots || spots.length === 0) return m;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const s of spots) {
        minX = Math.min(minX, s.spatial[0]);
        maxX = Math.max(maxX, s.spatial[0]);
        minY = Math.min(minY, s.spatial[1]);
        maxY = Math.max(maxY, s.spatial[1]);
    }
    const rx = spotSize[0], ry = spotSize[1];
    minX -= rx / 2; maxX += rx / 2;
    minY -= ry / 2; maxY += ry / 2;

    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;

    translate(m, m, [width / 2, height / 2]);
    scale(m, m, [1, -1]);
    translate(m, m, [-cx, -cy]);

    return m;
}

/** Reference image placement: the decoded texture centered unscaled. */
export function referenceImagePlacement(width: number, height: number, imgW: number, imgH: number) {
    const s = 1.0;
    const dx = (width - imgW * s) / 2;
    const dy = (height - imgH * s) / 2;

    const mRef = createIdentity();
    translate(mRef, mRef, [dx, dy]);
    scale(mRef, mRef, [s, s]);

    return { mRef, dx, dy, s };
}

/**
 * Both fits are anchored at the screen center, so when the canvas grows by
 * (dW, dH) the reference moves by half of it. Applying the same world
 * translation to the moving layer keeps inv(reference) · target unchanged.
 */
export function shiftForResize(m: mat3, dW: number, dH: number): mat3 {
  const t = createIdentity();
  translate(t, t, [dW / 2, dH / 2]);
  const out = mat3.create();
  multiply(out, t, m);
  return out;
}
