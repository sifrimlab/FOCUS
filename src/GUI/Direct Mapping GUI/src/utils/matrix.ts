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
