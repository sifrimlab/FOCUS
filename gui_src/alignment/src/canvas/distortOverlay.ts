/**
 * Distort frame of the moving layer, in screen space: the quad outline over
 * a halo, round corner handles and square edge handles. The handle being
 * dragged is filled with the accent and drawn larger. Display only.
 */
import type { Graphics } from 'pixi.js';
import type { CanvasPalette } from './canvasPalette';
import { edgeHandlePositions, type Point, type Quad } from './targetGeometry';

export function drawDistortOverlay(g: Graphics, corners: Point[], activeHandle: number, p: CanvasPalette) {
  g.clear();
  if (corners.length !== 4) return;
  const [h0, h1, h2, h3] = corners as Quad;

  const outline = () => g.moveTo(h0[0], h0[1]).lineTo(h1[0], h1[1]).lineTo(h2[0], h2[1]).lineTo(h3[0], h3[1]).lineTo(h0[0], h0[1]);
  outline().stroke({ width: 3.5, ...p.handleHalo });
  outline().stroke({ width: 1.5, ...p.handle });

  corners.forEach(([hx, hy], i) => {
    const active = activeHandle === i;
    g.circle(hx, hy, active ? 8 : 6).fill(active ? p.handle : p.handleFill).stroke({ width: 1.5, ...p.handle });
  });

  edgeHandlePositions(corners).forEach(([hx, hy], i) => {
    const active = activeHandle === i + 4;
    const size = active ? 6 : 4;
    g.rect(hx - size, hy - size, size * 2, size * 2).fill(active ? p.handle : p.handleFill).stroke({ width: 1.5, ...p.handle });
  });
}
