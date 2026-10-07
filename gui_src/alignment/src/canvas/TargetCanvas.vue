<script setup lang="ts">
/**
 * Moving layer (the "target"): places each sample at its initial fit,
 * renders the image or spots under the current transform, draws the distort
 * frame, executes panel commands and handles pointer input. On resize it
 * follows the reference's re-centering so an alignment in progress holds.
 * Watchers keep the original order, which fixes the flush order of store
 * updates within a tick.
 */
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { Graphics, type Application } from 'pixi.js';
import { mat3 } from 'gl-matrix';
import { useMainStore } from '../store/main';
import { usePixiLayer } from './usePixiLayer';
import { applyViewTransform, type ViewState } from './viewTransform';
import { shiftForResize, targetFitMatrix } from './layerFit';
import { createTargetGeometry, isProjective } from './targetGeometry';
import { applyTargetCommand } from './targetCommands';
import { useTargetPointer } from './useTargetPointer';
import { useTargetContent } from './useTargetContent';
import { drawDistortOverlay } from './distortOverlay';
import { useCanvasPalette } from './canvasPalette';

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
let wasProjective = false;

const layer = usePixiLayer(container, {
  onInit(app) {
    app.canvas.addEventListener('mousedown', pointer.onMouseDown);
    app.canvas.addEventListener('wheel', pointer.onWheel);
    overlay = new Graphics();
    app.stage.addChild(overlay);
    render();
  },
  // The canvas usually mounts after the sample has loaded, so the data watcher
  // has not fired: place the layer here once the size is final, then draw.
  async onSettle(app) {
    lastScreen = { ...app.screen };
    updateViewTransform();
    if (store.targetData) placeAtFit(app);
    await content.updateContent();
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
  app.render();
};

const drawOverlay = () => {
  if (!overlay) return;
  if (store.controlMode !== 'aligner') { overlay.clear(); return; }
  drawDistortOverlay(overlay, pointer.corners(), pointer.activeHandle(), palette.value);
};

/** Drops per-dataset caches and GPU objects. */
const resetLayer = () => {
  geometry.reset();
  wasProjective = false;
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
  } else {
    resetLayer();
  }
});

// Panel commands.
watch(() => store.pendingCommand, (cmd) => {
  const app = layer.app();
  if (!cmd || !app) return;
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

watch(() => [store.targetTransform, store.targetOpacity], () => {
  const isSpot = store.targetMeta?.modality_type !== 'IMAGE';
  const projective = isSpot && isProjective(store.targetTransform);
  // Redraw spots when projective, or on the one transition frame back to affine.
  if ((projective || wasProjective) && content.hasSpots()) content.drawSpots(projective);
  wasProjective = projective;
  content.updateContentTransform();
  render();
}, { deep: true });

watch(() => [store.globalZoom, store.viewOffset], () => {
  updateViewTransform();
  render();
}, { deep: true });

watch(() => [store.targetClassFilter, store.commonSpotBoost, store.targetSpotSize, store.targetForegroundMode], async () => {
  if (content.hasSpots()) {
    content.drawSpots(isProjective(store.targetTransform));
    render();
  } else {
    await content.updateContent();
    render();
  }
}, { deep: true });

watch(
  () => [store.targetTransform, store.globalZoom, store.viewOffset, store.controlMode],
  () => { drawOverlay(); layer.app()?.render(); },
  { deep: true },
);

watch(palette, () => { drawOverlay(); layer.app()?.render(); });

onMounted(() => {
  window.addEventListener('mouseup', pointer.onMouseUp);
  window.addEventListener('mousemove', pointer.onMouseMove);
});

onUnmounted(() => {
  pointer.dispose();
  window.removeEventListener('mouseup', pointer.onMouseUp);
  window.removeEventListener('mousemove', pointer.onMouseMove);
  content.release();
});
</script>

<template>
  <div ref="container" class="absolute inset-0 cursor-move" />
</template>
