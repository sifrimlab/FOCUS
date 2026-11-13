/**
 * Landmark warp overlay, in screen space: excluded regions, folded tissue,
 * the lasso being drawn, and the pins. Each pin is a ring where it is pinned
 * on the fixed layer and a dot where its moving-layer point currently lies,
 * joined when they differ (smoothing lets a pin give way a little). A pin
 * that disagrees with its neighbours is drawn in the warning colour.
 * Display only.
 */
import { Text, type Container, type Graphics } from 'pixi.js';
import type { useMainStore } from '../store/main';
import type { CanvasPalette } from './canvasPalette';
import { worldToScreen, type ViewState } from './viewTransform';
import { fixedToWorld, movingToWorld } from './warpView';
import type { Pt } from '../utils/warp';

type Store = ReturnType<typeof useMainStore>;

export const PIN_HIT_RADIUS = 10;

export interface LandmarkOverlayState {
  /** Fold cells (moving native centres) and their size. */
  folds: { centres: Pt[]; cell: Pt };
  /** Leave-one-out error per landmark and which are flagged. */
  flagged: boolean[];
  /** Screen points of the lasso being drawn. */
  lasso: Pt[];
  activePin: number;
  /** Interactive pins (landmark mode) are drawn larger and labelled. */
  interactive: boolean;
}

/** Screen position of each pin's fixed-layer destination. */
export function pinScreenPositions(store: Store, view: ViewState): Pt[] {
  return store.landmarks.map(l => {
    const [wx, wy] = fixedToWorld(store, l.dst[0], l.dst[1]);
    return worldToScreen(view, wx, wy);
  });
}

const movingScreen = (store: Store, view: ViewState, x: number, y: number): Pt => {
  const [wx, wy] = movingToWorld(store, x, y);
  return worldToScreen(view, wx, wy);
};

const fixedScreen = (store: Store, view: ViewState, x: number, y: number): Pt => {
  const [wx, wy] = fixedToWorld(store, x, y);
  return worldToScreen(view, wx, wy);
};

function drawPolygon(g: Graphics, pts: Pt[]) {
  if (pts.length < 2) return g;
  g.moveTo(pts[0]![0], pts[0]![1]);
  for (let i = 1; i < pts.length; i++) g.lineTo(pts[i]![0], pts[i]![1]);
  return g.closePath();
}

function drawExclusions(g: Graphics, store: Store, view: ViewState, p: CanvasPalette) {
  const polys = [
    ...store.exclusions.moving.map(poly => poly.map(([x, y]) => movingScreen(store, view, x, y))),
    ...store.exclusions.fixed.map(poly => poly.map(([x, y]) => fixedScreen(store, view, x, y))),
  ];
  for (const poly of polys) {
    drawPolygon(g, poly).fill({ color: p.handleHalo.color, alpha: 0.45 });
    drawPolygon(g, poly).stroke({ width: 1.5, ...p.handleFill });
  }
}

function drawFolds(g: Graphics, store: Store, view: ViewState, folds: LandmarkOverlayState['folds'], p: CanvasPalette) {
  const [hx, hy] = [folds.cell[0] / 2, folds.cell[1] / 2];
  for (const [cx, cy] of folds.centres) {
    const quad = [[cx - hx, cy - hy], [cx + hx, cy - hy], [cx + hx, cy + hy], [cx - hx, cy + hy]]
      .map(([x, y]) => movingScreen(store, view, x!, y!));
    drawPolygon(g, quad).fill({ color: p.danger.color, alpha: 0.35 });
  }
}

function drawPins(g: Graphics, store: Store, view: ViewState, s: LandmarkOverlayState, p: CanvasPalette) {
  const targets = pinScreenPositions(store, view);
  store.landmarks.forEach((l, i) => {
    const [tx, ty] = targets[i]!;
    const [cx, cy] = movingScreen(store, view, l.src[0], l.src[1]);
    const colour = s.flagged[i] ? p.warning : p.handle;
    if (Math.hypot(cx - tx, cy - ty) > 1) g.moveTo(cx, cy).lineTo(tx, ty).stroke({ width: 1.5, ...colour });
    g.circle(cx, cy, 2.5).fill(colour);
    const r = !s.interactive ? 4 : s.activePin === i ? 9 : 7;
    g.circle(tx, ty, r + 1.5).stroke({ width: 3, ...p.handleHalo });
    g.circle(tx, ty, r).stroke({ width: 2, ...colour });
  });
}

/** Pin numbers, reusing Text objects between frames. */
function drawLabels(labels: Container, store: Store, view: ViewState, s: LandmarkOverlayState, p: CanvasPalette) {
  const targets = s.interactive ? pinScreenPositions(store, view) : [];
  while (labels.children.length < targets.length) {
    labels.addChild(new Text({ text: '', style: { fontSize: 11, fontWeight: '600', fontFamily: 'system-ui, sans-serif' } }));
  }
  labels.children.forEach((child, i) => {
    const label = child as Text;
    label.visible = i < targets.length;
    if (!label.visible) return;
    label.text = String(i + 1);
    label.style.fill = s.flagged[i] ? p.warning.color : p.handle.color;
    label.position.set(targets[i]![0] + 10, targets[i]![1] - 18);
  });
}

export function drawLandmarkOverlay(g: Graphics, labels: Container, store: Store, view: ViewState, s: LandmarkOverlayState, p: CanvasPalette) {
  g.clear();
  if (store.targetMeta?.modality_type !== 'SPOT') { drawLabels(labels, store, view, { ...s, interactive: false }, p); return; }
  drawExclusions(g, store, view, p);
  drawFolds(g, store, view, s.folds, p);
  if (s.lasso.length > 1) {
    g.moveTo(s.lasso[0]![0], s.lasso[0]![1]);
    for (const [x, y] of s.lasso.slice(1)) g.lineTo(x, y);
    g.stroke({ width: 1.5, ...p.handle });
  }
  drawPins(g, store, view, s, p);
  drawLabels(labels, store, view, s, p);
}
