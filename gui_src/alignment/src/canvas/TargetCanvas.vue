<script setup lang="ts">
/**
 * Moving layer (the "target"): places each sample at its initial fit,
 * renders the image or spots under the current transform, draws the distort
 * frame, executes panel commands and handles pointer input. On resize it
 * follows the reference's re-centering so an alignment in progress holds.
 * It also owns the undo history, since the history includes `distort.before`.
 * With landmarks the spots are drawn through the landmark warp, and the
 * landmark overlay (pins, exclusions, folds) sits above the distort frame.
 * A warp confirmed in an earlier run is restored once both layers are placed.
 * Watchers keep the original order, which fixes the flush order of store
 * updates within a tick.
 */
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { Container, Graphics, type Application } from 'pixi.js';
import { mat3 } from 'gl-matrix';
import { useMainStore } from '../store/main';
import { usePixiLayer } from './usePixiLayer';
import { applyViewTransform, type ViewState } from './viewTransform';
import { shiftForResize, targetFitMatrix } from './layerFit';
import { createTargetGeometry } from './targetGeometry';
import { applyTargetCommand } from './targetCommands';
import { useTargetPointer } from './useTargetPointer';
import { useTargetContent } from './useTargetContent';
import { drawDistortOverlay } from './distortOverlay';
import { useCanvasPalette } from './canvasPalette';
import { useTransformHistory } from './useTransformHistory';
import { useLandmarkPointer } from './useLandmarkPointer';
import { drawLandmarkOverlay, type LandmarkOverlayState } from './landmarkOverlay';
import { borderAnchors, foldedCells, type Pt } from '../utils/warp';

const container = ref<HTMLElement | null>(null);
const store = useMainStore();
const palette = useCanvasPalette();
const geometry = createTargetGeometry({
  meta: () => store.targetMeta,
  data: () => store.targetData,
  spotSize: () => store.targetSpotSize,
});
const distort = { before: null as mat3 | null };
let initialFit: mat3 | null = null; // where "Reset transform" returns to
let lastScreen: { width: number; height: number } | null = null;
let overlay: Graphics | null = null;
let landmarkGfx: Graphics | null = null;
let landmarkLabels: Container | null = null;
let wasPointwise = false;
let placed = false; // the layer sits at its fit for the current sample
let folds: LandmarkOverlayState['folds'] = { centres: [], cell: [0, 0] };
const history = useTransformHistory(store, distort);

const layer = usePixiLayer(container, {
  onInit(app) {
    app.canvas.addEventListener('mousedown', onMouseDown);
    app.canvas.addEventListener('wheel', pointer.onWheel);
    app.canvas.addEventListener('contextmenu', landmarks.onContextMenu);
    overlay = new Graphics();
    landmarkGfx = new Graphics();
    landmarkLabels = new Container();
    app.stage.addChild(overlay);
    app.stage.addChild(landmarkGfx);
    app.stage.addChild(landmarkLabels);
    render();
  },
  // The canvas usually mounts after the sample has loaded, so the data watcher
  // has not fired: place the layer here once the size is final, then draw.
  async onSettle(app) {
    lastScreen = { ...app.screen };
    updateViewTransform();
    if (store.targetData) placeAtFit(app);
    await content.updateContent();
    restoreInitialState();
    render();
  },
  onResize(app) {
    followResize(app.screen);
    updateViewTransform();
    app.render();
  },
});

const view = (): ViewState => {
  const { width, height } = layer.app()!.screen;
  return { width, height, zoom: store.globalZoom, offset: store.viewOffset };
};

const pointer = useTargetPointer({ app: layer.app, store, geometry, view, distort });

const content = useTargetContent(layer, store);

const landmarks = useLandmarkPointer({
  app: layer.app, store, view,
  redraw: () => { drawOverlay(); layer.app()?.render(); },
});

/** Landmark and exclude tools take the pointer; the aligner and the camera keep theirs. */
const onMouseDown = (e: MouseEvent) => {
  if (store.controlMode === 'landmark' || store.controlMode === 'exclude') landmarks.onMouseDown(e);
  else pointer.onMouseDown(e);
};
const onMouseMove = (e: MouseEvent) => { pointer.onMouseMove(e); landmarks.onMouseMove(e); };
const onMouseUp = () => { pointer.onMouseUp(); landmarks.onMouseUp(); };

const updateViewTransform = () => {
  const v = layer.view();
  if (!v || !layer.app()) return;
  applyViewTransform(v, view());
};

const render = () => {
  const app = layer.app();
  if (!app || !app.renderer) return;
  updateViewTransform();
  content.updateContentTransform();
  content.cull();
  app.render();
};

const drawOverlay = () => {
  if (!overlay) return;
  if (store.controlMode !== 'aligner') overlay.clear();
  else drawDistortOverlay(overlay, pointer.corners(), pointer.activeHandle(), palette.value);
  if (landmarkGfx && landmarkLabels && layer.app()) {
    drawLandmarkOverlay(landmarkGfx, landmarkLabels, store, view(), {
      folds,
      flagged: store.landmarkFlags,
      lasso: landmarks.lasso() as Pt[],
      activePin: landmarks.activePin(),
      interactive: store.controlMode === 'landmark',
    }, palette.value);
  }
};

/** Re-tests the warp for folded tissue over the moving layer's extent. */
const updateFolds = () => {
  const bbox = geometry.getTargetBBox();
  if (!store.hasLandmarkWarp || !bbox) {
    folds = { centres: [], cell: [0, 0] };
  } else {
    folds = foldedCells(store.warp, {
      x: bbox.minX, y: bbox.minY, width: bbox.maxX - bbox.minX, height: bbox.maxY - bbox.minY,
    });
  }
  store.foldCount = folds.centres.length;
};

/**
 * Restores a warp confirmed in an earlier run (served at /initial_state): the
 * matrix maps moving native to fixed display, so the moving layer's world
 * transform is M_fixed · matrix. Waits for the fixed layer's fit.
 */
const restoreInitialState = () => {
  const state = store.initialState;
  if (!state || !placed || !store.referenceFitted || !store.targetData) return;
  store.initialState = null;
  const m = mat3.multiply(mat3.create(), store.referenceTransform, state.matrix as unknown as mat3);
  if (store.targetMeta?.modality_type === 'SPOT') {
    const lm = state.landmarks;
    store.landmarks = lm ? lm.src.map((src, i) => ({ src: [src[0], src[1]], dst: [lm.dst[i]![0], lm.dst[i]![1]] })) : [];
    if (state.smoothing !== undefined) store.smoothing = state.smoothing;
    store.exclusions = { moving: state.exclusions?.moving ?? [], fixed: state.exclusions?.fixed ?? [] };
  }
  store.updateTargetTransform(m);
  distort.before = mat3.clone(m);
  history.reset(m, distort.before);
};

/** Drops per-dataset caches and GPU objects. */
const resetLayer = () => {
  geometry.reset();
  wasPointwise = false;
  placed = false;
  folds = { centres: [], cell: [0, 0] };
  content.destroyChildren();
};

/** Places the layer at its fit, which also becomes the "Reset transform" target. */
const placeAtFit = (app: Application) => {
  resetLayer();
  const { width, height } = app.screen;
  const fitM = targetFitMatrix(width, height, store.globalZoom, store.targetMeta, store.targetData);
  store.updateTargetTransform(fitM);
  initialFit = mat3.clone(fitM);
  distort.before = mat3.clone(fitM);
  history.reset(fitM, fitM);
  const bbox = geometry.getTargetBBox();
  store.borderAnchors = bbox && store.targetMeta?.modality_type === 'SPOT'
    ? borderAnchors([bbox.minX, bbox.minY], [bbox.maxX, bbox.maxY])
    : [];
  placed = true;
};

/**
 * The reference re-centers on resize (its fit is anchored at the screen
 * center); apply the same shift to this layer and its saved transforms so
 * the alignment between the two layers is preserved.
 */
const followResize = (screen: { width: number; height: number }) => {
  if (lastScreen) {
    const dW = screen.width - lastScreen.width;
    const dH = screen.height - lastScreen.height;
    if (dW !== 0 || dH !== 0) {
      store.updateTargetTransform(shiftForResize(store.targetTransform, dW, dH));
      if (initialFit) initialFit = shiftForResize(initialFit, dW, dH);
      if (distort.before) distort.before = shiftForResize(distort.before, dW, dH);
      history.map(m => shiftForResize(m, dW, dH));
    }
  }
  lastScreen = { width: screen.width, height: screen.height };
};

// New dataset while mounted: place the layer at its fit and draw it.
watch(() => store.targetData, () => {
  const app = layer.app();
  if (app && store.targetData) {
    placeAtFit(app);
    content.updateContent();
    restoreInitialState();
  } else {
    resetLayer();
  }
});

// New spot view (resolution or feature image): rebuild the spot meshes with the new keys.
watch(() => store.targetDisplay, async () => {
  if (!content.hasSpots()) return;
  const boost = store.targetSpotBoost; // the rebuild resets it, the view change should not
  content.destroyChildren();
  await content.updateContent();
  store.setTargetSpotBoost(boost);
  render();
});

// Re-rendered channels: swap the sprite only; the alignment in progress is kept.
watch(() => store.targetImage, async () => {
  await content.updateContent();
  layer.app()?.render();
});

// Panel commands.
watch(() => store.pendingCommand, (cmd) => {
  const app = layer.app();
  if (!cmd || !app) return;
  // History commands restore stored states; they never reach the transform math.
  if (cmd.type === 'undo' || cmd.type === 'redo') {
    history[cmd.type]();
    store.pendingCommand = null;
    return;
  }
  const { width, height } = app.screen;
  const next = applyTargetCommand(cmd, {
    transform: store.targetTransform,
    width,
    height,
    globalZoom: store.globalZoom,
    localCenter: geometry.getLocalCenter(),
    transformBeforeDistort: distort.before,
    initialTransform: initialFit,
    initialScale: store.targetMeta?.scaling_factor || 1.0,
  });
  if (next) store.updateTargetTransform(next);
  store.pendingCommand = null;
});

// The fixed layer's fit arrives after the moving layer's: restore a stored warp then.
watch(() => [store.referenceFitted, store.initialState], restoreInitialState);

watch(() => [store.targetTransform, store.targetOpacity, store.referenceTransform, store.landmarks, store.smoothing, store.borderAnchors], () => {
  const pointwise = content.isPointwise();
  // Redraw spots when drawn per spot (projective or warped), or on the one frame back to affine.
  if ((pointwise || wasPointwise) && content.hasSpots()) content.drawSpots();
  wasPointwise = pointwise;
  updateFolds();
  content.updateContentTransform();
  render();
}, { deep: true });

watch(() => [store.globalZoom, store.viewOffset], () => {
  updateViewTransform();
  render();
}, { deep: true });

watch(() => [store.targetClassFilter, store.commonSpotBoost, store.targetSpotSize, store.targetForegroundMode], async () => {
  if (content.hasSpots()) {
    content.drawSpots();
    render();
  } else {
    await content.updateContent();
    render();
  }
}, { deep: true });

watch(
  () => [
    store.targetTransform, store.referenceTransform, store.globalZoom, store.viewOffset, store.controlMode,
    store.landmarks, store.smoothing, store.exclusions,
  ],
  () => { drawOverlay(); layer.app()?.render(); },
  { deep: true },
);

watch(palette, () => { drawOverlay(); layer.app()?.render(); });

onMounted(() => {
  window.addEventListener('mouseup', onMouseUp);
  window.addEventListener('mousemove', onMouseMove);
  window.addEventListener('keydown', landmarks.onKeyDown);
});

onUnmounted(() => {
  pointer.dispose();
  landmarks.dispose();
  window.removeEventListener('mouseup', onMouseUp);
  window.removeEventListener('mousemove', onMouseMove);
  window.removeEventListener('keydown', landmarks.onKeyDown);
  content.release();
});
</script>

<template>
  <div ref="container" class="absolute inset-0 cursor-move" />
</template>
