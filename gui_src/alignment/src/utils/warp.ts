/**
 * Landmark warp of the moving layer: the layer matrix refined by a regularised
 * thin-plate spline. Mirrors `src/focus/spatial/thin_plate.py` and `warp.py` exactly (the parity
 * fixture `tests/fixtures/warp_parity.json` keeps the two in step).
 *
 *   W(x) = R(H x)
 *
 * Points on the moving layer's outline (`anchors`, corners and edge midpoints)
 * are held where H puts them unless a landmark is near, so a landmark bends the
 * tissue around it and the overall placement stays with H (the Aligner tool).
 *
 * H is the pair matrix (moving native -> fixed-layer display coordinates) and R
 * the residual map fitted from H·src -> dst:
 *   0 landmarks: identity; 1: translation; 2 (or collinear): similarity;
 *   3+: thin-plate spline with an affine part, fitted in coordinates centred on
 *   the landmarks and scaled by their RMS spread, smoothing added to the kernel
 *   diagonal (so it has no units).
 */
import type { mat3 } from 'gl-matrix';
import { projectiveTransformPoint } from './matrix';

export type Pt = [number, number];

/** One landmark: `src` on the moving layer (native), `dst` on the fixed layer (display). */
export interface Landmark { src: Pt; dst: Pt }

/** Exclusion polygons, each in its own layer's coordinates (moving native / fixed display). */
export interface Exclusions { moving: Pt[][]; fixed: Pt[][] }

export const DEFAULT_SMOOTHING = 1e-3;
const COLLINEAR_TOL = 1e-9;

export type ResidualKind = 'identity' | 'translation' | 'similarity' | 'tps';

/** Residual map R. `affine` rows: [offset, d/dux, d/duy], each [x, y]. */
export interface ResidualMap {
  kind: ResidualKind;
  centre: Pt;
  spread: number;
  nodes: Pt[];
  weights: Pt[];
  affine: [Pt, Pt, Pt];
}

const identityAffine = (): [Pt, Pt, Pt] => [[0, 0], [1, 0], [0, 1]];

/** Thin-plate kernel r^2 log r from a squared distance (0 at r = 0). */
function kernel(r2: number): number {
  return r2 > 0 ? 0.5 * r2 * Math.log(r2) : 0;
}

/** Solve A X = B for X (A n×n, B n×m) by Gaussian elimination with partial pivoting. */
export function solveLinear(A: number[][], B: number[][]): number[][] | null {
  const n = A.length;
  const m = B[0]?.length ?? 0;
  const a = A.map(r => r.slice());
  const b = B.map(r => r.slice());
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(a[r]![col]!) > Math.abs(a[piv]![col]!)) piv = r;
    if (!(Math.abs(a[piv]![col]!) > 1e-300)) return null;
    if (piv !== col) {
      [a[col], a[piv]] = [a[piv]!, a[col]!];
      [b[col], b[piv]] = [b[piv]!, b[col]!];
    }
    const rowC = a[col]!;
    const bC = b[col]!;
    const p = rowC[col]!;
    for (let r = col + 1; r < n; r++) {
      const rowR = a[r]!;
      const f = rowR[col]! / p;
      if (f === 0) continue;
      for (let j = col; j < n; j++) rowR[j] = rowR[j]! - f * rowC[j]!;
      const bR = b[r]!;
      for (let j = 0; j < m; j++) bR[j] = bR[j]! - f * bC[j]!;
    }
  }
  const x = Array.from({ length: n }, () => new Array(m).fill(0) as number[]);
  for (let r = n - 1; r >= 0; r--) {
    for (let j = 0; j < m; j++) {
      let s = b[r]![j]!;
      for (let c = r + 1; c < n; c++) s -= a[r]![c]! * x[c]![j]!;
      x[r]![j] = s / a[r]![r]!;
    }
  }
  if (!x.every(r => r.every(Number.isFinite))) return null;
  return x;
}

function collinear(u: Pt[]): boolean {
  let mx = 0, my = 0;
  for (const [x, y] of u) { mx += x; my += y; }
  mx /= u.length; my /= u.length;
  let sxx = 0, sxy = 0, syy = 0;
  for (const [x, y] of u) {
    const dx = x - mx, dy = y - my;
    sxx += dx * dx; sxy += dx * dy; syy += dy * dy;
  }
  const tr = sxx + syy;
  const disc = Math.sqrt(Math.max(0, (sxx - syy) * (sxx - syy) / 4 + sxy * sxy));
  const l1 = tr / 2 + disc;
  const l2 = Math.max(0, tr / 2 - disc);
  return Math.sqrt(l2) <= COLLINEAR_TOL * Math.max(Math.sqrt(l1), 1e-300);
}

/** Least-squares similarity v = a·u + b in complex form, as an affine block. */
function similarityFit(u: Pt[], v: Pt[]): [Pt, Pt, Pt] {
  const n = u.length;
  let mur = 0, mui = 0, mvr = 0, mvi = 0;
  for (let i = 0; i < n; i++) { mur += u[i]![0]; mui += u[i]![1]; mvr += v[i]![0]; mvi += v[i]![1]; }
  mur /= n; mui /= n; mvr /= n; mvi /= n;
  let den = 0, nr = 0, ni = 0;
  for (let i = 0; i < n; i++) {
    const dur = u[i]![0] - mur, dui = u[i]![1] - mui;
    const dvr = v[i]![0] - mvr, dvi = v[i]![1] - mvi;
    den += dur * dur + dui * dui;
    // conj(du) * dv
    nr += dur * dvr + dui * dvi;
    ni += dur * dvi - dui * dvr;
  }
  const ar = den > 0 ? nr / den : 1;
  const ai = den > 0 ? ni / den : 0;
  const br = mvr - (ar * mur - ai * mui);
  const bi = mvi - (ar * mui + ai * mur);
  return [[br, bi], [ar, ai], [-ai, ar]];
}

/** Fit the residual map sending `p` onto `q`. */
export function fitResidual(p: Pt[], q: Pt[], smoothing: number): ResidualMap {
  const n = p.length;
  const base = { centre: [0, 0] as Pt, spread: 1, nodes: [] as Pt[], weights: [] as Pt[] };
  if (n === 0) return { kind: 'identity', ...base, affine: identityAffine() };
  if (n === 1) {
    const a = identityAffine();
    a[0] = [q[0]![0] - p[0]![0], q[0]![1] - p[0]![1]];
    return { kind: 'translation', ...base, affine: a };
  }
  let cx = 0, cy = 0;
  for (const [x, y] of p) { cx += x; cy += y; }
  cx /= n; cy /= n;
  let ss = 0;
  for (const [x, y] of p) ss += (x - cx) * (x - cx) + (y - cy) * (y - cy);
  const spread = Math.sqrt(ss / n);
  if (!(spread > 0) || !Number.isFinite(spread)) {
    let tx = 0, ty = 0;
    for (let i = 0; i < n; i++) { tx += q[i]![0] - p[i]![0]; ty += q[i]![1] - p[i]![1]; }
    const a = identityAffine();
    a[0] = [tx / n, ty / n];
    return { kind: 'translation', ...base, affine: a };
  }
  const centre: Pt = [cx, cy];
  const u = p.map(([x, y]) => [(x - cx) / spread, (y - cy) / spread] as Pt);
  const v = q.map(([x, y]) => [(x - cx) / spread, (y - cy) / spread] as Pt);
  const similarity = (): ResidualMap =>
    ({ kind: 'similarity', centre, spread, nodes: [], weights: [], affine: similarityFit(u, v) });
  if (n === 2 || collinear(u)) return similarity();

  const N = n + 3;
  const A = Array.from({ length: N }, () => new Array(N).fill(0) as number[]);
  const B = Array.from({ length: N }, () => [0, 0]);
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const dx = u[i]![0] - u[j]![0], dy = u[i]![1] - u[j]![1];
      A[i]![j] = kernel(dx * dx + dy * dy);
    }
    A[i]![i] = A[i]![i]! + smoothing;
    A[i]![n] = 1; A[i]![n + 1] = u[i]![0]; A[i]![n + 2] = u[i]![1];
    A[n]![i] = 1; A[n + 1]![i] = u[i]![0]; A[n + 2]![i] = u[i]![1];
    B[i] = [v[i]![0], v[i]![1]];
  }
  const sol = solveLinear(A, B);
  if (!sol) return similarity();
  const weights = sol.slice(0, n).map(r => [r[0]!, r[1]!] as Pt);
  const affine = [sol[n]!, sol[n + 1]!, sol[n + 2]!].map(r => [r[0]!, r[1]!] as Pt) as [Pt, Pt, Pt];
  return { kind: 'tps', centre, spread, nodes: u, weights, affine };
}

/** Apply the residual map to one point. */
export function applyResidual(r: ResidualMap, x: number, y: number): Pt {
  const a = r.affine;
  if (r.kind === 'identity' || r.kind === 'translation') return [x + a[0][0], y + a[0][1]];
  const ux = (x - r.centre[0]) / r.spread;
  const uy = (y - r.centre[1]) / r.spread;
  let ox = a[0][0] + ux * a[1][0] + uy * a[2][0];
  let oy = a[0][1] + ux * a[1][1] + uy * a[2][1];
  if (r.kind === 'tps') {
    const nodes = r.nodes, w = r.weights;
    for (let k = 0; k < nodes.length; k++) {
      const dx = ux - nodes[k]![0], dy = uy - nodes[k]![1];
      const phi = kernel(dx * dx + dy * dy);
      ox += phi * w[k]![0];
      oy += phi * w[k]![1];
    }
  }
  return [r.centre[0] + r.spread * ox, r.centre[1] + r.spread * oy];
}

/** Jacobian of the residual map at one point, row-major [dxdx, dxdy, dydx, dydy]. */
export function residualJacobian(r: ResidualMap, x: number, y: number): [number, number, number, number] {
  const a = r.affine;
  let j00 = a[1][0], j01 = a[2][0], j10 = a[1][1], j11 = a[2][1];
  if (r.kind === 'tps') {
    const ux = (x - r.centre[0]) / r.spread;
    const uy = (y - r.centre[1]) / r.spread;
    for (let k = 0; k < r.nodes.length; k++) {
      const dx = ux - r.nodes[k]![0], dy = uy - r.nodes[k]![1];
      const r2 = dx * dx + dy * dy;
      if (!(r2 > 0)) continue;
      const g = Math.log(r2) + 1;
      const w = r.weights[k]!;
      j00 += w[0] * g * dx; j01 += w[0] * g * dy;
      j10 += w[1] * g * dx; j11 += w[1] * g * dy;
    }
  }
  return [j00, j01, j10, j11];
}

/** A fitted warp: moving native -> fixed-layer display coordinates. */
export interface Warp {
  matrix: mat3;
  residual: ResidualMap;
  /** True when the warp is not one projective matrix (a spline is in use). */
  nonrigid: boolean;
  map(x: number, y: number): Pt;
}

/**
 * An outline anchor within this fraction of the outline's diagonal of a landmark is
 * released, so a landmark near the border can still move it.
 */
export const ANCHOR_RELEASE = 0.15;

/** Outline anchors of a box: its corners, then its edge midpoints. */
export function borderAnchors(lo: Pt, hi: Pt): Pt[] {
  const [x0, y0] = lo, [x1, y1] = hi;
  const xm = (x0 + x1) / 2, ym = (y0 + y1) / 2;
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [xm, y0], [x1, ym], [xm, y1], [x0, ym]];
}

/** The outline anchors that hold: none without landmarks, none near a landmark (order kept). */
export function activeAnchors(anchors: Pt[], landmarks: Landmark[]): Pt[] {
  if (!anchors.length || !landmarks.length) return [];
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [x, y] of anchors) {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  const radius = ANCHOR_RELEASE * Math.hypot(maxX - minX, maxY - minY);
  return anchors.filter(([ax, ay]) => {
    let nearest = Infinity;
    for (const l of landmarks) nearest = Math.min(nearest, Math.hypot(ax - l.src[0], ay - l.src[1]));
    return nearest > radius;
  });
}

/**
 * Fit the warp of `matrix` (gl-matrix, column-major) refined by `landmarks`. The
 * held outline `anchors` (moving native) stay where the matrix puts them, so a
 * landmark bends the tissue around it instead of moving the whole layer.
 */
export function buildWarp(
  matrix: mat3, landmarks: Landmark[], smoothing: number = DEFAULT_SMOOTHING, anchors: Pt[] = [],
): Warp {
  const held = activeAnchors(anchors, landmarks).map(([x, y]) => projectiveTransformPoint(matrix, x, y));
  const p = [...landmarks.map(l => projectiveTransformPoint(matrix, l.src[0], l.src[1])), ...held];
  const q = [...landmarks.map(l => l.dst), ...held];
  const residual = fitResidual(p, q, smoothing);
  const plain = residual.kind === 'identity';
  return {
    matrix,
    residual,
    nonrigid: residual.kind === 'tps',
    map: plain
      ? (x, y) => projectiveTransformPoint(matrix, x, y)
      : (x, y) => {
          const [hx, hy] = projectiveTransformPoint(matrix, x, y);
          return applyResidual(residual, hx, hy);
        },
  };
}

/**
 * Landmarks that disagree with their neighbours: a leave-one-out error well above the
 * typical one (2.5 × the median) and above 2 display pixels. Needs 4+ landmarks.
 */
export function flagOutliers(errors: number[]): boolean[] {
  const finite = errors.filter(Number.isFinite).sort((a, b) => a - b);
  if (finite.length < 4) return errors.map(() => false);
  const mid = finite.length >> 1;
  const median = finite.length % 2 ? finite[mid]! : (finite[mid - 1]! + finite[mid]!) / 2;
  return errors.map(e => Number.isFinite(e) && e > 2.5 * median && e > 2);
}

/**
 * Display-pixel error of each landmark predicted from all the others (NaN with < 2).
 * The outline anchors held by the full set of landmarks stay in every fit.
 */
export function leaveOneOut(
  matrix: mat3, landmarks: Landmark[], smoothing: number = DEFAULT_SMOOTHING, anchors: Pt[] = [],
): number[] {
  const n = landmarks.length;
  if (n < 2) return new Array(n).fill(NaN);
  const p = landmarks.map(l => projectiveTransformPoint(matrix, l.src[0], l.src[1]));
  const q = landmarks.map(l => l.dst);
  const held = activeAnchors(anchors, landmarks).map(([x, y]) => projectiveTransformPoint(matrix, x, y));
  return landmarks.map((_, i) => {
    const keep = (_x: unknown, k: number) => k !== i;
    const fit = fitResidual([...p.filter(keep), ...held], [...q.filter(keep), ...held], smoothing);
    const [x, y] = applyResidual(fit, p[i]![0], p[i]![1]);
    return Math.hypot(x - q[i]![0], y - q[i]![1]);
  });
}

/**
 * Fold test on a grid over the moving layer's native bounding box: true where the
 * residual turns tissue inside out (Jacobian determinant <= 0). Returns the
 * centres of the folded cells (native coordinates) and the cell size.
 */
export function foldedCells(
  warp: Warp,
  bbox: { x: number; y: number; width: number; height: number },
  cells = 48,
): { centres: Pt[]; cell: Pt } {
  const cell: Pt = [bbox.width / cells, bbox.height / cells];
  const centres: Pt[] = [];
  if (warp.residual.kind !== 'tps') return { centres, cell };
  for (let i = 0; i < cells; i++) {
    for (let j = 0; j < cells; j++) {
      const x = bbox.x + (i + 0.5) * cell[0];
      const y = bbox.y + (j + 0.5) * cell[1];
      const [hx, hy] = projectiveTransformPoint(warp.matrix, x, y);
      const [a, b, c, d] = residualJacobian(warp.residual, hx, hy);
      if (!(a * d - b * c > 0)) centres.push([x, y]);
    }
  }
  return { centres, cell };
}

/** Even-odd point-in-polygon test. */
export function insidePolygon(poly: Pt[], x: number, y: number): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i]!;
    const [xj, yj] = poly[j]!;
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** True when a point lies inside any of the polygons. */
export function insideAny(polys: Pt[][], x: number, y: number): boolean {
  for (const p of polys) if (p.length >= 3 && insidePolygon(p, x, y)) return true;
  return false;
}

/** Jacobian of a projective matrix (gl-matrix, column-major) at one point, row-major. */
export function homographyJacobian(m: mat3, x: number, y: number): [number, number, number, number] {
  let w = m[2] * x + m[5] * y + m[8];
  if (w === 0) w = 1;
  const X = (m[0] * x + m[3] * y + m[6]) / w;
  const Y = (m[1] * x + m[4] * y + m[7]) / w;
  return [
    (m[0] - X * m[2]) / w, (m[3] - X * m[5]) / w,
    (m[1] - Y * m[2]) / w, (m[4] - Y * m[5]) / w,
  ];
}

/**
 * Moving-native point that the warp sends to fixed display point (qx, qy):
 * the inverse matrix gives the start, Newton steps on W refine it. Null when
 * it does not converge (inside a fold, or a singular matrix).
 */
export function invertWarp(warp: Warp, qx: number, qy: number, tolPx = 1e-6): Pt | null {
  const inv = mat3Invert(warp.matrix);
  if (!inv) return null;
  let [x, y] = projectiveTransformPoint(inv, qx, qy);
  if (warp.residual.kind === 'identity') return [x, y];
  for (let it = 0; it < 40; it++) {
    const [fx, fy] = warp.map(x, y);
    const rx = fx - qx, ry = fy - qy;
    if (Math.hypot(rx, ry) <= tolPx) return [x, y];
    const [hx, hy] = projectiveTransformPoint(warp.matrix, x, y);
    const [a, b, c, d] = residualJacobian(warp.residual, hx, hy);
    const [e, f, g, h] = homographyJacobian(warp.matrix, x, y);
    // J = R'(Hx) · H'(x)
    const j00 = a * e + b * g, j01 = a * f + b * h, j10 = c * e + d * g, j11 = c * f + d * h;
    const det = j00 * j11 - j01 * j10;
    if (!(Math.abs(det) > 1e-300)) return null;
    x -= (j11 * rx - j01 * ry) / det;
    y -= (-j10 * rx + j00 * ry) / det;
  }
  const [fx, fy] = warp.map(x, y);
  return Math.hypot(fx - qx, fy - qy) <= 1e-3 ? [x, y] : null;
}

function mat3Invert(m: mat3): mat3 | null {
  const out = new Float64Array(9) as unknown as mat3;
  const [a00, a01, a02, a10, a11, a12, a20, a21, a22] = [m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8]];
  const b01 = a22 * a11 - a12 * a21;
  const b11 = -a22 * a10 + a12 * a20;
  const b21 = a21 * a10 - a11 * a20;
  const det = a00 * b01 + a01 * b11 + a02 * b21;
  if (!det) return null;
  const id = 1 / det;
  out[0] = b01 * id; out[1] = (-a22 * a01 + a02 * a21) * id; out[2] = (a12 * a01 - a02 * a11) * id;
  out[3] = b11 * id; out[4] = (a22 * a00 - a02 * a20) * id; out[5] = (-a12 * a00 + a02 * a10) * id;
  out[6] = b21 * id; out[7] = (-a21 * a00 + a01 * a20) * id; out[8] = (a11 * a00 - a01 * a10) * id;
  return out;
}
