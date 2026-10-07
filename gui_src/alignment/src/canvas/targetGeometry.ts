/**
 * Geometry of the moving layer: its local center and bounding box (cached
 * per dataset), the screen positions of the distort handles, and the hit
 * zones used by the pointer controller. Arithmetic kept as in the original.
 */
import type { mat3 } from 'gl-matrix';
import type { Metadata, SpotModalityPayload } from '../api/types';
import { projectiveTransformPoint } from '../utils/matrix';
import { worldToScreen, type ViewState } from './viewTransform';

export type Point = [number, number];
export type Quad = [Point, Point, Point, Point];
export interface BBox { minX: number; minY: number; maxX: number; maxY: number }

export const HANDLE_HIT_RADIUS = 15;
export const ROTATE_ZONE_OUTER = 30; // px — annular zone outside handle but near corner triggers rotation
// Maps each edge handle index (0-3) to the pair of corner indices it connects:
// 0=top (corners 0,1), 1=right (corners 1,2), 2=bottom (corners 2,3), 3=left (corners 3,0)
export const EDGE_TO_CORNERS: [number, number][] = [[0, 1], [1, 2], [2, 3], [3, 0]];

export const isProjective = (m: mat3): boolean => Math.abs(m[2]) > 1e-10 || Math.abs(m[5]) > 1e-10;

export interface TargetSource {
  meta: () => Metadata | null;
  data: () => unknown;
  spotSize: () => [number, number];
}

/** Local center and bounding box of the moving layer, cached until `reset()`. */
export function createTargetGeometry(src: TargetSource) {
  let cachedLocalCenter: [number, number] | null = null;
  let cachedLocalBBox: BBox | null = null;

  const getLocalCenter = () => {
    if (cachedLocalCenter) return cachedLocalCenter;
    const meta = src.meta();
    if (!meta) return [0, 0];
    if (meta.modality_type === 'IMAGE') {
       const [h, w] = meta.image_shape || [0, 0];
       cachedLocalCenter = [w/2, h/2];
       return cachedLocalCenter;
    } else {
       const data = src.data() as SpotModalityPayload;
       if (!data) return [0, 0];
       let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
       for (const s of data) {
          minX = Math.min(minX, s.spatial[0]);
          maxX = Math.max(maxX, s.spatial[0]);
          minY = Math.min(minY, s.spatial[1]);
          maxY = Math.max(maxY, s.spatial[1]);
       }
       if (minX === Infinity) return [0, 0];
       cachedLocalCenter = [(minX + maxX) / 2, (minY + maxY) / 2];
       return cachedLocalCenter;
    }
  };

  const getTargetBBox = () => {
    if (cachedLocalBBox) return cachedLocalBBox;
    const meta = src.meta();
    if (!meta) return null;
    if (meta.modality_type === 'IMAGE') {
      const [h, w] = meta.image_shape || [0, 0];
      cachedLocalBBox = { minX: 0, minY: 0, maxX: w, maxY: h };
    } else {
      const data = src.data() as SpotModalityPayload;
      if (!data) return null;
      const [rx, ry] = src.spotSize();
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const s of data) {
        minX = Math.min(minX, s.spatial[0]); maxX = Math.max(maxX, s.spatial[0]);
        minY = Math.min(minY, s.spatial[1]); maxY = Math.max(maxY, s.spatial[1]);
      }
      cachedLocalBBox = { minX: minX - rx/2, minY: minY - ry/2, maxX: maxX + rx/2, maxY: maxY + ry/2 };
    }
    return cachedLocalBBox;
  };

  const reset = () => {
    cachedLocalCenter = null;
    cachedLocalBBox = null;
  };

  return { getLocalCenter, getTargetBBox, reset };
}

export const bboxCorners = (bbox: BBox): Quad => [
  [bbox.minX, bbox.minY], [bbox.maxX, bbox.minY],
  [bbox.maxX, bbox.maxY], [bbox.minX, bbox.maxY],
];

/** Screen positions of the four corner handles, or [] when there is no layer. */
export function handleScreenPositions(bbox: BBox | null, m: mat3, view: ViewState): Point[] {
  if (!bbox) return [];
  return bboxCorners(bbox).map(([lx, ly]) => {
    const [wx, wy] = projectiveTransformPoint(m, lx, ly);
    return worldToScreen(view, wx, wy);
  });
}

export function edgeHandlePositions(corners: Point[]): Point[] {
  if (corners.length !== 4) return [];
  const [h0, h1, h2, h3] = corners as Quad;
  return [
    [(h0[0] + h1[0]) / 2, (h0[1] + h1[1]) / 2],
    [(h1[0] + h2[0]) / 2, (h1[1] + h2[1]) / 2],
    [(h2[0] + h3[0]) / 2, (h2[1] + h3[1]) / 2],
    [(h3[0] + h0[0]) / 2, (h3[1] + h0[1]) / 2],
  ];
}

export function isInsideQuad(corners: Point[], mx: number, my: number): boolean {
  if (corners.length !== 4) return false;
  // Cross product sign must be consistent for all edges of the (possibly non-axis-aligned) quad
  let sign: number | null = null;
  for (let i = 0; i < 4; i++) {
    const [ax, ay] = corners[i]!;
    const [bx, by] = corners[(i + 1) % 4]!;
    const cross = (bx - ax) * (my - ay) - (by - ay) * (mx - ax);
    const s = cross > 0 ? 1 : cross < 0 ? -1 : 0;
    if (s === 0) continue;
    if (sign === null) sign = s;
    else if (sign !== s) return false;
  }
  return true;
}

export function isInRotateZone(corners: Point[], mx: number, my: number): boolean {
  if (isInsideQuad(corners, mx, my)) return false;
  for (const h of corners) {
    const d = Math.hypot(mx - h[0], my - h[1]);
    if (d >= HANDLE_HIT_RADIUS && d < ROTATE_ZONE_OUTER) return true;
  }
  return false;
}

/** Index of the handle under the pointer: 0-3 corners, 4-7 edges, -1 none. */
export function hitHandle(corners: Point[], mx: number, my: number): number {
  for (let i = 0; i < corners.length; i++) {
    const h = corners[i]!;
    if (Math.hypot(mx - h[0], my - h[1]) < HANDLE_HIT_RADIUS) return i;
  }
  const edges = edgeHandlePositions(corners);
  for (let i = 0; i < edges.length; i++) {
    const h = edges[i]!;
    if (Math.hypot(mx - h[0], my - h[1]) < HANDLE_HIT_RADIUS) return i + 4;
  }
  return -1;
}
