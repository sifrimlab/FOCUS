/**
 * Pointer control of the landmark and exclusion tools.
 *
 * Landmark mode: press on the moving layer to pin the structure under the
 * cursor and drag it onto its counterpart on the fixed layer; a click without
 * a drag leaves an anchor pin that holds that area in place. Press on an
 * existing pin to move it again. Right-click (or Delete while hovering) removes a pin.
 *
 * Exclude mode: drag a lasso around tissue that has no counterpart in the other
 * section (a tear, a fold, a missing piece) on the layer chosen in the panel.
 * Right-click inside a region removes it.
 *
 * In both modes the wheel zooms and the middle button pans the view.
 */
import type { Application } from 'pixi.js';
import type { useMainStore } from '../store/main';
import { screenToWorld, type ViewState } from './viewTransform';
import { pinScreenPositions, PIN_HIT_RADIUS } from './landmarkOverlay';
import { fixedToWorld, movingToWorld, worldToFixed, worldToMoving } from './warpView';
import { insidePolygon, type Pt } from '../utils/warp';

type Store = ReturnType<typeof useMainStore>;

const LASSO_STEP_PX = 4;

export interface LandmarkPointerDeps {
  app: () => Application | null;
  store: Store;
  view: () => ViewState;
  /** Redraws the overlay (lasso progress, hover). */
  redraw: () => void;
}

export function useLandmarkPointer({ app, store, view, redraw }: LandmarkPointerDeps) {
  let dragPin = -1;
  let hoverPin = -1;
  let panning = false;
  let last: Pt = [0, 0];
  let lasso: Pt[] = [];
  let pendingDst: Pt | null = null;
  let rafId: number | null = null;

  const active = () => store.controlMode === 'landmark' || store.controlMode === 'exclude';

  const local = (e: MouseEvent): Pt | null => {
    const a = app();
    if (!a) return null;
    const rect = a.canvas.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  };

  const hitPin = ([mx, my]: Pt) => {
    const pins = pinScreenPositions(store, view());
    let best = -1;
    let bestD = PIN_HIT_RADIUS;
    pins.forEach(([x, y], i) => {
      const d = Math.hypot(x - mx, y - my);
      if (d <= bestD) { best = i; bestD = d; }
    });
    return best;
  };

  const flushPin = () => {
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
    if (dragPin >= 0 && pendingDst) store.moveLandmark(dragPin, pendingDst);
    pendingDst = null;
  };

  const startPin = (pt: Pt) => {
    const hit = hitPin(pt);
    if (hit >= 0) { dragPin = hit; return; }
    const [wx, wy] = screenToWorld(view(), pt[0], pt[1]);
    const src = worldToMoving(store, wx, wy);
    if (!src) return; // inside a fold: the point under the cursor is ambiguous
    // Pinned where it currently lies, so a click alone anchors it in place.
    const [fx, fy] = store.warp.map(src[0], src[1]);
    store.addLandmark({ src, dst: [fx, fy] });
    dragPin = store.landmarks.length - 1;
  };

  const onMouseDown = (e: MouseEvent) => {
    if (!active()) return;
    const pt = local(e);
    if (!pt) return;
    if (e.button === 1) { panning = true; last = [e.clientX, e.clientY]; e.preventDefault(); return; }
    if (e.button !== 0) return;
    if (store.controlMode === 'landmark') startPin(pt);
    else lasso = [pt];
    redraw();
  };

  const onMouseMove = (e: MouseEvent) => {
    if (!active()) return;
    if (panning) {
      store.updateViewOffset(e.clientX - last[0], e.clientY - last[1]);
      last = [e.clientX, e.clientY];
      return;
    }
    const pt = local(e);
    if (!pt) return;
    if (dragPin >= 0) {
      const [wx, wy] = screenToWorld(view(), pt[0], pt[1]);
      pendingDst = worldToFixed(store, wx, wy);
      if (rafId === null) rafId = requestAnimationFrame(() => { rafId = null; flushPin(); });
      return;
    }
    if (lasso.length) {
      const prev = lasso[lasso.length - 1]!;
      if (Math.hypot(pt[0] - prev[0], pt[1] - prev[1]) >= LASSO_STEP_PX) { lasso.push(pt); redraw(); }
      return;
    }
    const hover = store.controlMode === 'landmark' ? hitPin(pt) : -1;
    if (hover !== hoverPin) { hoverPin = hover; redraw(); }
    const a = app();
    if (a) a.canvas.style.cursor = hover >= 0 ? 'grab' : 'crosshair';
  };

  /** Lasso screen points -> polygon in the chosen layer's frame; null if any vertex is unmappable. */
  const lassoPolygon = (): Pt[] | null => {
    const out: Pt[] = [];
    for (const [sx, sy] of lasso) {
      const [wx, wy] = screenToWorld(view(), sx, sy);
      const p = store.excludeLayer === 'moving' ? worldToMoving(store, wx, wy) : worldToFixed(store, wx, wy);
      if (!p) return null;
      out.push(p);
    }
    return out;
  };

  const onMouseUp = () => {
    panning = false;
    if (dragPin >= 0) { flushPin(); dragPin = -1; }
    if (lasso.length) {
      const poly = lasso.length >= 3 ? lassoPolygon() : null;
      if (poly) store.addExclusion(store.excludeLayer, poly);
      lasso = [];
    }
    redraw();
  };

  /** The exclusion under a screen point, searched on both layers. */
  const hitExclusion = (pt: Pt): { layer: 'moving' | 'fixed'; index: number } | null => {
    const [wx, wy] = screenToWorld(view(), pt[0], pt[1]);
    const toWorld = { moving: movingToWorld, fixed: fixedToWorld } as const;
    for (const layer of ['moving', 'fixed'] as const) {
      const polys = store.exclusions[layer];
      for (let i = polys.length - 1; i >= 0; i--) {
        const worldPoly = polys[i]!.map(([x, y]) => toWorld[layer](store, x, y));
        if (insidePolygon(worldPoly, wx, wy)) return { layer, index: i };
      }
    }
    return null;
  };

  const onContextMenu = (e: MouseEvent) => {
    if (!active()) return;
    e.preventDefault();
    const pt = local(e);
    if (!pt) return;
    if (store.controlMode === 'landmark') {
      const hit = hitPin(pt);
      if (hit >= 0) { store.removeLandmark(hit); hoverPin = -1; }
    } else {
      const hit = hitExclusion(pt);
      if (hit) store.removeExclusion(hit.layer, hit.index);
    }
    redraw();
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (store.controlMode !== 'landmark' || hoverPin < 0) return;
    if (e.key === 'Delete' || e.key === 'Backspace') {
      const t = e.target;
      if (t instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return;
      e.preventDefault();
      store.removeLandmark(hoverPin);
      hoverPin = -1;
      redraw();
    }
  };

  const dispose = () => { if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; } };

  return {
    onMouseDown, onMouseMove, onMouseUp, onContextMenu, onKeyDown, dispose,
    activePin: () => (dragPin >= 0 ? dragPin : hoverPin),
    lasso: () => lasso,
  };
}
