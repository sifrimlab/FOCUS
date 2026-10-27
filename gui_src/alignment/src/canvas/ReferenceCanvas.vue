<script setup lang="ts">
/**
 * Fixed layer (the "reference"): renders the image or spots at their fit
 * and publishes that fit as store.referenceTransform, which the export
 * inverts. Display-only otherwise; it receives no pointer input.
 */
import { ref, watch } from 'vue';
import { Graphics, Sprite, Texture } from 'pixi.js';
import { useMainStore } from '../store/main';
import type { SpotModalityPayload } from '../api/types';
import { usePixiLayer } from './usePixiLayer';
import { applyViewTransform } from './viewTransform';
import { referenceImagePlacement, referenceSpotFitMatrix } from './layerFit';
import { drawReferenceSpots } from './spotLayer';
import { applyReferenceContentTransform } from './contentTransform';
import { NativeSpotLayer } from './nativeSpotLayer';
import { classColor } from '../utils/colors';

const container = ref<HTMLElement | null>(null);
const store = useMainStore();
let nativeSpots: NativeSpotLayer | null = null;

const fitSpots = (width: number, height: number) =>
  referenceSpotFitMatrix(width, height, store.referenceMeta, store.referenceData, store.referenceSpotSize);

const layer = usePixiLayer(container, {
  onInit() {
    render();
  },
  // After layout settles, recompute the fit and redraw with the final dimensions.
  onSettle(app) {
    updateViewTransform();
    if (store.referenceData && store.referenceMeta && store.referenceMeta.modality_type === 'SPOT') {
      const { width, height } = app.screen;
      store.updateReferenceTransform(fitSpots(width, height));
    }
    updateContent();
  },
  // Keep the centroid at the screen center after a resize.
  onResize(app) {
    updateViewTransform();
    if (store.referenceData && store.referenceMeta && store.referenceMeta.modality_type === 'SPOT') {
      const { width, height } = app.screen;
      store.updateReferenceTransform(fitSpots(width, height));
    }
    updateContent();
    app.render();
  },
});

const updateViewTransform = () => {
  const app = layer.app();
  const v = layer.view();
  if (!v || !app || !app.renderer) return;
  const { width, height } = app.screen;
  applyViewTransform(v, { width, height, zoom: store.globalZoom, offset: store.viewOffset });
  nativeSpots?.cull(app.screen);
};

const render = async () => {
  const app = layer.app();
  const content = layer.content();
  if (!app || !layer.view() || !content) return;

  if (app.screen.width === 0 || app.screen.height === 0) {
    app.resize();
  }

  if (!store.referenceData || !store.referenceMeta) {
    content.removeChildren();
    return;
  }

  updateViewTransform();
};

const updateContent = async () => {
  const app = layer.app();
  const content = layer.content();
  if (!app || !content) return;

  const children = content.removeChildren();
  children.forEach(child => child.destroy({ children: true }));
  nativeSpots = null;
  // Reset the container transform; IMAGE positions its sprite directly,
  // SPOT drives it through store.referenceTransform.
  content.position.set(0, 0);
  content.scale.set(1, 1);
  content.rotation = 0;

  if (!store.referenceData || !store.referenceMeta) return;

  const { width, height } = app.screen;

  if (store.referenceMeta.modality_type === 'IMAGE') {
    const url = URL.createObjectURL(store.referenceData as Blob);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      URL.revokeObjectURL(url);
      const texture = Texture.from(img);
      const sprite = new Sprite(texture);

      const { mRef, dx, dy, s } = referenceImagePlacement(width, height, texture.width, texture.height);
      sprite.x = dx;
      sprite.y = dy;
      sprite.scale.set(s);

      content.addChild(sprite);
      store.updateReferenceTransform(mRef);
      if (app && app.renderer) app.render();
    } catch (e) {
      console.error('Failed to load texture', e);
    }
  } else if (store.referenceMeta.modality_type === 'SPOT') {
    const spots = store.referenceData as SpotModalityPayload;
    if (!spots || spots.length === 0) return;

    store.setReferenceSpotBoost(1.0);
    const style = {
      spotSize: store.referenceSpotSize,
      boost: store.commonSpotBoost,
      classFilter: store.referenceClassFilter,
      foregroundMode: store.referenceForegroundMode,
      basis: store.referenceMeta.spot_basis,
      colorOf: (cls: number) => classColor(store.referenceMeta, cls, false),
    };
    if (store.referenceMeta.spot_encoding) {
      nativeSpots = new NativeSpotLayer(spots, store.referenceMeta, false);
      content.addChild(nativeSpots);
      nativeSpots.update(style);
    } else {
      const graphics = new Graphics();
      content.addChild(graphics);
      drawReferenceSpots(graphics, spots, style);
    }

    applyReferenceContentTransform(content, store.referenceTransform);
    nativeSpots?.cull(app.screen);
    if (app && app.renderer) app.render();
  }
};

watch(() => [store.globalZoom, store.viewOffset], () => {
  updateViewTransform();
  const app = layer.app();
  if (app && app.renderer) app.render();
});

// New data: compute and persist the fit before drawing, so updateContent can
// apply it even if called again later (settle, resize).
watch(() => [store.referenceData, store.referenceMeta], () => {
  const app = layer.app();
  if (app && store.referenceData && store.referenceMeta) {
    if (store.referenceMeta.modality_type === 'SPOT') {
      const { width, height } = app.screen;
      if (width > 0 && height > 0) {
        store.updateReferenceTransform(fitSpots(width, height));
      }
    }
  }
  updateContent();
  if (app && app.renderer) app.render();
});

watch(() => [store.referenceClassFilter, store.commonSpotBoost, store.referenceSpotSize, store.referenceForegroundMode], async () => {
  await updateContent();
  const app = layer.app();
  if (app && app.renderer) app.render();
}, { deep: true });
</script>

<template>
  <div ref="container" class="absolute inset-0 pointer-events-none" />
</template>
