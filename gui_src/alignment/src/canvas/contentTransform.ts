/**
 * Applies a layer transform (gl-matrix mat3) to a Pixi container by
 * decomposing it into position, rotation, skew and scale. Display only.
 * The two layers keep their original, slightly different decompositions.
 */
import type { Container } from 'pixi.js';
import type { mat3 } from 'gl-matrix';
import { isProjective } from './targetGeometry';

export function applyTargetContentTransform(content: Container, m: mat3, opacity: number) {
    content.alpha = opacity;

    if (isProjective(m)) {
      // Projective: spots are drawn at world positions directly; container stays at identity
      content.position.set(0, 0);
      content.rotation = 0;
      content.skew.set(0, 0);
      content.scale.set(1, 1);
      return;
    }

    const a = m[0]; const b = m[1]; const c = m[3]; const d = m[4];
    const tx = m[6]; const ty = m[7];
    const rotationX = Math.atan2(b, a);
    const rotationY = Math.atan2(-c, d);
    content.position.set(tx, ty);
    content.rotation = rotationX;
    content.skew.x = rotationX - rotationY;
    content.skew.y = 0;
    content.scale.set(Math.sqrt(a * a + b * b), Math.sqrt(c * c + d * d));
}

export function applyReferenceContentTransform(content: Container, m: mat3) {
    const a = m[0], b = m[1];
    const c = m[3], d = m[4];
    const tx = m[6], ty = m[7];

    content.position.set(tx, ty);
    content.rotation = Math.atan2(b, a);
    content.scale.set(Math.sqrt(a * a + b * b), Math.sqrt(c * c + d * d));

    const det = a * d - b * c;
    if (det < 0) content.scale.y *= -1;
}
