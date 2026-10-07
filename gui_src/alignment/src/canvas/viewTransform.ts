/**
 * Camera (view) transform shared by both canvases: the view is centered on
 * the screen, scaled by the global zoom and shifted by the view offset.
 */
import type { Container } from 'pixi.js';

export interface ViewState {
  width: number;
  height: number;
  zoom: number;
  offset: [number, number];
}

export function applyViewTransform(view: Container, v: ViewState) {
  const cx = v.width / 2;
  const cy = v.height / 2;

  view.position.set(cx, cy);
  view.scale.set(v.zoom);
  view.pivot.set(cx - v.offset[0], cy - v.offset[1]);
}

export function worldToScreen(v: ViewState, wx: number, wy: number): [number, number] {
  const cx = v.width / 2; const cy = v.height / 2;
  return [
    (wx - (cx - v.offset[0])) * v.zoom + cx,
    (wy - (cy - v.offset[1])) * v.zoom + cy,
  ];
}

export function screenToWorld(v: ViewState, sx: number, sy: number): [number, number] {
  const cx = v.width / 2; const cy = v.height / 2;
  return [
    (sx - cx) / v.zoom + cx - v.offset[0],
    (sy - cy) / v.zoom + cy - v.offset[1],
  ];
}
