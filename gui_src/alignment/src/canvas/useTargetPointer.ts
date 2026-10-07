/**
 * Pointer and wheel control of the moving layer.
 * Aligner mode: drag a corner (perspective), an edge (two corners) or the
 * frame (translate); drag just outside a corner to rotate; wheel scales
 * about the cursor. Camera mode: drag pans the view; wheel zooms it.
 * Matrix arithmetic is kept exactly as in the original canvas.
 */
import { mat3 } from 'gl-matrix';
import type { Application } from 'pixi.js';
import type { useMainStore } from '../store/main';
import { computeHomography, projectiveTransformPoint } from '../utils/matrix';
import { screenToWorld, type ViewState } from './viewTransform';
import {
  bboxCorners, handleScreenPositions, hitHandle, isInRotateZone, isInsideQuad, isProjective,
  type createTargetGeometry,
} from './targetGeometry';
import { draggedCorners, rotateDragTransform, wheelScaleTransform } from './targetDrag';

// Rotation cursor: 270° clockwise arc (top→left) with a downward arrowhead, hotspot at center.
// An OS cursor, drawn in fixed black and white for contrast on any image.
const ROTATE_CURSOR = (() => {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">' +
    '<path d="M13 4 A9 9 0 1 1 4 13" fill="none" stroke="black" stroke-width="3" stroke-linecap="round"/>' +
    '<path d="M13 4 A9 9 0 1 1 4 13" fill="none" stroke="white" stroke-width="1.5" stroke-linecap="round"/>' +
    '<polygon points="4,17 1,11 7,11" fill="white" stroke="black" stroke-width="0.5"/>' +
    '</svg>';
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}") 12 12, auto`;
})();

const ROTATE_HANDLE = 9;
const FRAME_HANDLE = 8;

export interface TargetPointerDeps {
  app: () => Application | null;
  store: ReturnType<typeof useMainStore>;
  geometry: ReturnType<typeof createTargetGeometry>;
  view: () => ViewState;
  /** Affine transform saved before the first perspective drag ("Reset distortion"). */
  distort: { before: mat3 | null };
}

export function useTargetPointer({ app, store, geometry, view, distort }: TargetPointerDeps) {
  let isDragging = false;
  let lastX = 0;
  let lastY = 0;
  let activeHandleIndex = -1; // 0-3: corner handles, 4-7: edge handles, 8: translate, 9: rotate
  let dragStartWorldCorners: [number, number][] = [];
  let dragStartMouseWorld: [number, number] = [0, 0];
  let latestHomography: mat3 | null = null; // RAF-throttled distort drag state
  let distortRafId: number | null = null;

  const corners = () => handleScreenPositions(geometry.getTargetBBox(), store.targetTransform, view());

  const updateHoverCursor = (mx: number, my: number) => {
    const a = app();
    if (!a) return;
    if (store.controlMode !== 'aligner') {
      a.canvas.style.cursor = '';
      return;
    }
    const c = corners();
    if (hitHandle(c, mx, my) >= 0) { a.canvas.style.cursor = 'grab'; return; }
    if (isInRotateZone(c, mx, my)) { a.canvas.style.cursor = ROTATE_CURSOR; return; }
    a.canvas.style.cursor = isInsideQuad(c, mx, my) ? 'move' : 'default';
  };

  const startDistortDrag = (handleIdx: number, mx: number, my: number, e: MouseEvent) => {
    // Snapshot the current affine transform before the first projective corner/edge drag.
    // This lets "Reset Distortion" undo only the projective warp, preserving panel adjustments.
    if (handleIdx < 8 && !isProjective(store.targetTransform)) {
      distort.before = mat3.clone(store.targetTransform);
    }
    activeHandleIndex = handleIdx;
    isDragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    const a = app();
    if (a) a.canvas.style.cursor = 'grabbing';
    const bbox = geometry.getTargetBBox();
    if (bbox) {
      const m = store.targetTransform;
      dragStartWorldCorners = bboxCorners(bbox).map(([lx, ly]) => projectiveTransformPoint(m, lx, ly));
    }
    dragStartMouseWorld = screenToWorld(view(), mx, my);
  };

  const onMouseDown = (e: MouseEvent) => {
    const a = app();
    if (store.controlMode === 'aligner' && a) {
      const rect = a.canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const c = corners();

      // Corner handles first (0-3), then edge handles (4-7)
      const handle = hitHandle(c, mx, my);
      if (handle >= 0) {
        startDistortDrag(handle, mx, my, e);
        return;
      }

      // Rotation zone: near corner, outside polygon → rotate
      if (isInRotateZone(c, mx, my)) {
        activeHandleIndex = ROTATE_HANDLE;
        isDragging = true;
        lastX = e.clientX;
        lastY = e.clientY;
        a.canvas.style.cursor = 'grabbing';
        return;
      }

      // Inside frame → translate all
      if (isInsideQuad(c, mx, my)) {
        startDistortDrag(FRAME_HANDLE, mx, my, e);
        return;
      }
      return;
    }
    isDragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
  };

  const rotateDrag = (a: Application, e: MouseEvent) => {
    const rect = a.canvas.getBoundingClientRect();
    store.updateTargetTransform(rotateDragTransform(
      rect, geometry.getLocalCenter(), store.targetTransform, view(),
      { x: lastX, y: lastY }, e,
    ));
    lastX = e.clientX;
    lastY = e.clientY;
  };

  const distortDrag = (a: Application, e: MouseEvent) => {
    if (dragStartWorldCorners.length !== 4) return;
    const rect = a.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const currentMouseWorld = screenToWorld(view(), mx, my);

    // Total displacement in world space from drag start
    const dx = currentMouseWorld[0] - dragStartMouseWorld[0];
    const dy = currentMouseWorld[1] - dragStartMouseWorld[1];

    const newWorldCorners = draggedCorners(dragStartWorldCorners, activeHandleIndex, dx, dy);

    const bbox = geometry.getTargetBBox();
    if (!bbox) return;

    const H = computeHomography(bboxCorners(bbox), newWorldCorners);
    if (!H) return;

    // RAF-throttle store updates: compute H on every mouse move but only push
    // to the reactive store once per animation frame. This caps the watcher
    // chain (drawSpots + render) at 60 fps regardless of mouse polling rate.
    latestHomography = H;
    if (distortRafId === null) {
      distortRafId = requestAnimationFrame(() => {
        distortRafId = null;
        if (latestHomography) {
          store.updateTargetTransform(latestHomography);
          latestHomography = null;
        }
      });
    }
  };

  const onMouseMove = (e: MouseEvent) => {
    const a = app();
    if (!isDragging && a) {
      const rect = a.canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      if (mx >= 0 && my >= 0 && mx <= rect.width && my <= rect.height) {
        updateHoverCursor(mx, my);
      } else if (store.controlMode === 'aligner') {
        a.canvas.style.cursor = 'default';
      }
    }
    if (!isDragging) return;

    if (store.controlMode === 'aligner' && activeHandleIndex >= 0) {
      if (!a) return;
      if (activeHandleIndex === ROTATE_HANDLE) rotateDrag(a, e);
      else distortDrag(a, e);
      return;
    }

    if (store.controlMode === 'camera') {
        const screenDx = e.clientX - lastX;
        const screenDy = e.clientY - lastY;
        lastX = e.clientX;
        lastY = e.clientY;
        store.updateViewOffset(screenDx, screenDy);
    }
  };

  const onMouseUp = () => {
    // Flush any RAF-pending homography so the final drag position is applied
    if (distortRafId !== null) {
      cancelAnimationFrame(distortRafId);
      distortRafId = null;
      if (latestHomography) {
        store.updateTargetTransform(latestHomography);
        latestHomography = null;
      }
    }
    isDragging = false;
    activeHandleIndex = -1;
    dragStartWorldCorners = [];
    dragStartMouseWorld = [0, 0];
    const a = app();
    if (a) a.canvas.style.cursor = '';
  };

  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const zoom = e.deltaY > 0 ? 0.98 : 1.02;

    if (store.controlMode === 'camera') {
        store.globalZoom *= zoom;
        return;
    }

    const a = app();
    if (!a) return;
    const rect = a.canvas.getBoundingClientRect();
    store.updateTargetTransform(wheelScaleTransform(rect, e, zoom, store.viewOffset, store.globalZoom, store.targetTransform));
  };

  /** Stops a pending RAF update (unmount). */
  const dispose = () => {
    if (distortRafId !== null) { cancelAnimationFrame(distortRafId); distortRafId = null; }
  };

  return {
    onMouseDown, onMouseMove, onMouseUp, onWheel, dispose,
    activeHandle: () => activeHandleIndex,
    corners,
  };
}
