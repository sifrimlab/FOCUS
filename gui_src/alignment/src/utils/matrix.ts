import { mat3 } from 'gl-matrix';

export type TransformMatrix = mat3;

export function createIdentity(): TransformMatrix {
  return mat3.create();
}

export function clone(a: TransformMatrix): TransformMatrix {
  return mat3.clone(a);
}

export function translate(out: TransformMatrix, a: TransformMatrix, v: [number, number]): void {
  mat3.translate(out, a, v);
}

export function scale(out: TransformMatrix, a: TransformMatrix, v: [number, number]): void {
  mat3.scale(out, a, v);
}

export function rotate(out: TransformMatrix, a: TransformMatrix, rad: number): void {
  mat3.rotate(out, a, rad);
}

export function invert(out: TransformMatrix, a: TransformMatrix): TransformMatrix | null {
  return mat3.invert(out, a);
}

export function multiply(out: TransformMatrix, a: TransformMatrix, b: TransformMatrix): void {
  mat3.multiply(out, a, b);
}

export function transformPoint(m: TransformMatrix, p: [number, number]): [number, number] {
  const x = p[0];
  const y = p[1];
  const tx = x * m[0] + y * m[3] + m[6];
  const ty = x * m[1] + y * m[4] + m[7];
  return [tx, ty];
}

// Solve 8x8 linear system Ax = b via Gaussian elimination with partial pivoting
function solveLinear8(A: number[][], b: number[]): number[] | null {
  const n = 8;
  const aug = A.map((row, i) => [...row, b[i]!]);
  for (let col = 0; col < n; col++) {
    let maxRow = col;
    for (let row = col + 1; row < n; row++) {
      if (Math.abs(aug[row]![col]!) > Math.abs(aug[maxRow]![col]!)) maxRow = row;
    }
    [aug[col], aug[maxRow]] = [aug[maxRow]!, aug[col]!];
    if (Math.abs(aug[col]![col]!) < 1e-10) return null;
    const pivot = aug[col]![col]!;
    for (let row = col + 1; row < n; row++) {
      const f = aug[row]![col]! / pivot;
      for (let j = col; j <= n; j++) aug[row]![j]! -= f * aug[col]![j]!;
    }
  }
  const x = new Array(n).fill(0) as number[];
  for (let row = n - 1; row >= 0; row--) {
    x[row] = aug[row]![n]!;
    for (let col = row + 1; col < n; col++) x[row] = x[row]! - aug[row]![col]! * x[col]!;
    x[row] = x[row]! / aug[row]![row]!;
  }
  return x;
}

// Compute the 3x3 homography (projective transform) mapping 4 src points to 4 dst points.
// Uses DLT with H[2][2]=1 constraint → 8 unknowns, 8 equations.
// Returns a gl-matrix mat3 (column-major) or null if the configuration is degenerate.
export function computeHomography(
  src: [[number,number],[number,number],[number,number],[number,number]],
  dst: [[number,number],[number,number],[number,number],[number,number]]
): mat3 | null {
  const A: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i++) {
    const [sx, sy] = src[i]!;
    const [dx, dy] = dst[i]!;
    // x' equation: h0*sx + h1*sy + h2 - h6*sx*dx - h7*sy*dx = dx
    A.push([sx, sy, 1, 0, 0, 0, -sx * dx, -sy * dx]);
    b.push(dx);
    // y' equation: h3*sx + h4*sy + h5 - h6*sx*dy - h7*sy*dy = dy
    A.push([0, 0, 0, sx, sy, 1, -sx * dy, -sy * dy]);
    b.push(dy);
  }
  const h = solveLinear8(A, b);
  if (!h) return null;
  // Mathematical H (row-major): [[h0,h1,h2],[h3,h4,h5],[h6,h7,1]]
  // gl-matrix col-major: col0=[h0,h3,h6], col1=[h1,h4,h7], col2=[h2,h5,1]
  return mat3.fromValues(h[0]!, h[3]!, h[6]!, h[1]!, h[4]!, h[7]!, h[2]!, h[5]!, 1);
}

// Apply a (possibly projective) mat3 to a 2D point.
// For affine matrices (m[2]=m[5]=0, m[8]=1) this is identical to transformPoint.
export function projectiveTransformPoint(m: mat3, lx: number, ly: number): [number, number] {
  const X = m[0] * lx + m[3] * ly + m[6];
  const Y = m[1] * lx + m[4] * ly + m[7];
  const W = m[2] * lx + m[5] * ly + m[8];
  return Math.abs(W) < 1e-10 ? [X, Y] : [X / W, Y / W];
}
