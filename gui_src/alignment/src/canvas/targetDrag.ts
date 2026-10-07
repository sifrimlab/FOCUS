/**
 * Transform arithmetic of the pointer gestures on the moving layer: rotate
 * drag, corner/edge/frame drag targets, and wheel scaling. Kept exactly as
 * in the original canvas, since the results feed the exported alignment.
 */
import { mat3 } from 'gl-matrix';
import { scale, translate, multiply, rotate } from '../utils/matrix';
import { EDGE_TO_CORNERS, type Quad } from './targetGeometry';
import { worldToScreen, type ViewState } from './viewTransform';

interface Rect { left: number; top: number; width: number; height: number }

/** Incremental rotation about the layer's affine center, from the last to the current pointer position (screen angles). */
export function rotateDragTransform(
  rect: Rect,
  localCenter: number[],
  mCurrent: mat3,
  view: ViewState,
  last: { x: number; y: number },
  e: { clientX: number; clientY: number },
): mat3 {
  const cx_target_world = (localCenter[0] || 0) * mCurrent[0] + (localCenter[1] || 0) * mCurrent[3] + mCurrent[6];
  const cy_target_world = (localCenter[0] || 0) * mCurrent[1] + (localCenter[1] || 0) * mCurrent[4] + mCurrent[7];
  // Same camera mapping as the handles, so the angle follows the pointer when panned or zoomed.
  const [cx_target_screen, cy_target_screen] = worldToScreen(view, cx_target_world, cy_target_world);
  const mx_screen = e.clientX - rect.left;
  const my_screen = e.clientY - rect.top;
  const angleOld = Math.atan2(last.y - rect.top - cy_target_screen, last.x - rect.left - cx_target_screen);
  const angleNew = Math.atan2(my_screen - cy_target_screen, mx_screen - cx_target_screen);
  const dAngle = angleNew - angleOld;
  const m = mat3.create();
  translate(m, m, [cx_target_world, cy_target_world]);
  rotate(m, m, dAngle);
  translate(m, m, [-cx_target_world, -cy_target_world]);
  const newM = mat3.create();
  multiply(newM, m, mCurrent);
  return newM;
}

/** World corners after moving the grabbed handle by (dx, dy): 0-3 one corner, 4-7 an edge, else the frame. */
export function draggedCorners(start: [number, number][], handle: number, dx: number, dy: number): Quad {
  if (handle < 4) {
    // Corner drag: only the grabbed corner moves
    return start.map((c, i) =>
      i === handle ? [c[0] + dx, c[1] + dy] as [number, number] : c as [number, number]
    ) as Quad;
  } else if (handle < 8) {
    // Edge drag: both corners connected to this edge move together
    const cornerPair = EDGE_TO_CORNERS[handle - 4]!;
    return start.map((c, i) =>
      cornerPair.includes(i) ? [c[0] + dx, c[1] + dy] as [number, number] : c as [number, number]
    ) as Quad;
  }
  // Inside-frame drag: translate all 4 corners uniformly
  return start.map(c =>
    [c[0] + dx, c[1] + dy] as [number, number]
  ) as Quad;
}

/** Wheel scaling of the layer about the cursor position (in world space). */
export function wheelScaleTransform(
  rect: Rect,
  e: { clientX: number; clientY: number },
  zoom: number,
  viewOffset: [number, number],
  globalZoom: number,
  current: mat3,
): mat3 {
  const mx_screen = e.clientX - rect.left;
  const my_screen = e.clientY - rect.top;

  const cx = rect.width / 2;
  const cy = rect.height / 2;

  const mx = (mx_screen - cx - viewOffset[0] * globalZoom) / globalZoom + cx;
  const my = (my_screen - cy - viewOffset[1] * globalZoom) / globalZoom + cy;

  const m = mat3.create();
  translate(m, m, [mx, my]);
  scale(m, m, [zoom, zoom]);
  translate(m, m, [-mx, -my]);

  const newM = mat3.create();
  multiply(newM, m, current);
  return newM;
}
