/**
 * Panel commands on the moving layer (flip, rotate, scale, resets). Returns
 * the new transform, or null when the command leaves it unchanged. Flip,
 * rotate and scale pivot about the layer's projected local center. The
 * arithmetic is kept exactly as in the original canvas.
 */
import { mat3 } from 'gl-matrix';
import { createIdentity, scale, translate, multiply, rotate, projectiveTransformPoint } from '../utils/matrix';
import type { PendingCommand } from '../store/main';

export interface CommandContext {
  transform: mat3;
  width: number;
  height: number;
  globalZoom: number;
  localCenter: number[];
  transformBeforeDistort: mat3 | null;
  /** The layer's initial fit, where "Reset transform" returns to (identity if not placed yet). */
  initialTransform: mat3 | null;
  /** Scale restored by `resetScale` (metadata scaling factor, or 1). */
  initialScale: number;
}

export function applyTargetCommand(cmd: PendingCommand, ctx: CommandContext): mat3 | null {
  const { width, height, localCenter } = ctx;

  const cx_screen = (width / ctx.globalZoom) / 2;
  const cy_screen = (height / ctx.globalZoom) / 2;

  const mCurrent = ctx.transform;
  // Use projective-aware center computation so flip/rotate/scale pivot is correct
  // even after a perspective distortion has been applied.
  const [cx_target, cy_target] = projectiveTransformPoint(mCurrent, localCenter[0] || 0, localCenter[1] || 0);

  const m = mat3.create();

  if (cmd.type === 'reset') {
     return ctx.initialTransform ? mat3.clone(ctx.initialTransform) : createIdentity();
  } else if (cmd.type === 'resetDistort') {
     if (ctx.transformBeforeDistort) return mat3.clone(ctx.transformBeforeDistort);
  } else if (cmd.type === 'zoom') {
     translate(m, m, [cx_screen, cy_screen]);
     scale(m, m, [cmd.value, cmd.value]);
     translate(m, m, [-cx_screen, -cy_screen]);
     const newM = mat3.create();
     multiply(newM, m, ctx.transform);
     return newM;
  } else if (cmd.type === 'rotate') {
     translate(m, m, [cx_target, cy_target]);
     rotate(m, m, cmd.value);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, ctx.transform);
     return newM;
  } else if (cmd.type === 'flip') {
     translate(m, m, [cx_target, cy_target]);
     scale(m, m, cmd.value ? [-1, 1] : [1, -1]);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, ctx.transform);
     return newM;
  } else if (cmd.type === 'setScale') {
     const currentM = ctx.transform;
     const currentScale = Math.hypot(currentM[0], currentM[1]);
     const targetScale = cmd.value;
     if (currentScale !== 0) {
        const ratio = targetScale / currentScale;
        translate(m, m, [cx_target, cy_target]);
        scale(m, m, [ratio, ratio]);
        translate(m, m, [-cx_target, -cy_target]);
        const newM = mat3.create();
        multiply(newM, m, ctx.transform);
        return newM;
     }
  } else if (cmd.type === 'setRotation') {
     const currentM = ctx.transform;
     const currentRot = Math.atan2(currentM[1], currentM[0]);
     const targetRot = cmd.value * Math.PI / 180;
     const delta = targetRot - currentRot;

     translate(m, m, [cx_target, cy_target]);
     rotate(m, m, delta);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, ctx.transform);
     return newM;
  } else if (cmd.type === 'resetScale') {
     const initialScale = ctx.initialScale;
     const currentM = ctx.transform;
     const currentScale = Math.hypot(currentM[0], currentM[1]);
     if (currentScale !== 0) {
        const ratio = initialScale / currentScale;
        translate(m, m, [cx_target, cy_target]);
        scale(m, m, [ratio, ratio]);
        translate(m, m, [-cx_target, -cy_target]);
        const newM = mat3.create();
        multiply(newM, m, ctx.transform);
        return newM;
     }
  } else if (cmd.type === 'resetRotation') {
     const currentM = ctx.transform;
     const s = Math.hypot(currentM[0], currentM[1]);
     const tx = cx_target - s * (localCenter[0] || 0);
     const ty = cy_target - s * (localCenter[1] || 0);
     return mat3.fromValues(s, 0, 0, 0, s, 0, tx, ty, 1);
  }
  return null;
}
