<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { Application, Container, Sprite, Graphics, Texture } from 'pixi.js';
import { useMainStore } from '../store/main';
import { createIdentity, scale, translate } from '../utils/matrix';
import { getReferenceColor } from '../utils/colors';
import type { SpotModalityPayload } from '../api/types';

const canvasContainer = ref<HTMLElement | null>(null);
const store = useMainStore();
let app: Application | null = null;
let viewContainer: Container | null = null;
let contentContainer: Container | null = null;
let resizeObserver: ResizeObserver | null = null;

const initPixi = async () => {
  if (!canvasContainer.value) return;
  
  // Destroy existing app if any
  if (app) {
      try {
        app.destroy(true, { children: true, texture: true });
      } catch (e) { console.error('Error destroying app', e); }
      app = null;
  }
  
  const newApp = new Application();
  try {
      await newApp.init({ 
        resizeTo: canvasContainer.value, 
        backgroundAlpha: 0.1, // Slight background to see the canvas
        backgroundColor: 0x1a1a1a,
        antialias: true,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
        preference: 'webgl',
        autoStart: false
      });
  } catch (e) {
      console.error('Pixi init failed', e);
      return;
  }
  
  if (!canvasContainer.value) {
      newApp.destroy(true);
      return;
  }
  
  app = newApp;
  canvasContainer.value.appendChild(app.canvas);
  app.canvas.style.width = '100%';
  app.canvas.style.height = '100%';
  app.canvas.style.display = 'block';
  
  viewContainer = new Container();
  app.stage.addChild(viewContainer);
  
  contentContainer = new Container();
  viewContainer.addChild(contentContainer);
  
  // Initial render
  render();
  
  // Force a resize/update after a short delay to ensure layout is settled
  setTimeout(() => {
      if (app && app.renderer) {
          app.resize();
          updateViewTransform();
          updateContent();
      }
  }, 100);
};

const updateViewTransform = () => {
  if (!viewContainer || !app || !app.renderer) return;
  
  const { width, height } = app.screen;
  const cx = width / 2;
  const cy = height / 2;
  
  viewContainer.position.set(cx, cy);
  viewContainer.scale.set(store.globalZoom);
  viewContainer.pivot.set(cx - store.viewOffset[0], cy - store.viewOffset[1]);
};

const render = async () => {
  if (!app || !viewContainer || !contentContainer) return;
  
  // Ensure we have dimensions
  if (app.screen.width === 0 || app.screen.height === 0) {
      app.resize();
  }

  if (!store.referenceData || !store.referenceMeta) {
    contentContainer.removeChildren();
    return;
  }

  // Update View Transform
  updateViewTransform();
};

const updateContent = async () => {
  if (!app || !contentContainer) return;
  
  contentContainer.removeChildren();
  
  if (!store.referenceData || !store.referenceMeta) return;

  const { width, height } = app.screen;
  console.log('ReferenceCanvas updateContent', width, height, store.referenceMeta.modality_type);

  const mRef = createIdentity();

  if (store.referenceMeta.modality_type === 'IMAGE') {
    // ... existing image code ...
    const imgBlob = store.referenceData as Blob;
    const url = URL.createObjectURL(imgBlob);
    
    try {
        const img = new Image();
        img.src = url;
        await img.decode();
        URL.revokeObjectURL(url);
        const texture = Texture.from(img);
        const sprite = new Sprite(texture);
        
        const imgW = texture.width;
        const imgH = texture.height;
        const s = 1.0;
        const dx = (width - imgW * s) / 2;
        const dy = (height - imgH * s) / 2;
        
        translate(mRef, mRef, [dx, dy]);
        scale(mRef, mRef, [s, s]);
        
        sprite.x = dx;
        sprite.y = dy;
        sprite.scale.set(s);
        
        contentContainer.addChild(sprite);
        store.updateReferenceTransform(mRef);
        if (app && app.renderer) app.render();
    } catch (e) {
        console.error("Failed to load texture", e);
    }
  } else if (store.referenceMeta.modality_type === 'SPOT') {
    const data = store.referenceData as SpotModalityPayload;
    const spots = data;
    if (!spots || spots.length === 0) return;

    const spotSize = store.referenceSpotSize;
    
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const s of spots) {
      minX = Math.min(minX, s.spatial[0]);
      maxX = Math.max(maxX, s.spatial[0]);
      minY = Math.min(minY, s.spatial[1]);
      maxY = Math.max(maxY, s.spatial[1]);
    }
    
    const rx = spotSize[0];
    const ry = spotSize[1];
    minX -= rx/2; maxX += rx/2;
    minY -= ry/2; maxY += ry/2;
    
    const s = 1.0;
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    
    // Center logic:
    // 1. Translate to center of screen
    // 2. Scale (and flip Y)
    // 3. Translate to center of content (inverse)
    translate(mRef, mRef, [width/2, height/2]);
    scale(mRef, mRef, [s, -s]);
    translate(mRef, mRef, [-cx, -cy]);
    
    store.updateReferenceTransform(mRef);
    
    // Apply transform to a container for spots
    const spotsContainer = new Container();
    
    // Use explicit transform properties
    spotsContainer.position.set(mRef[6], mRef[7]);
    spotsContainer.scale.set(mRef[0], mRef[4]);
    
    contentContainer.addChild(spotsContainer);
    
    // Draw spots
    const graphics = new Graphics();
    spotsContainer.addChild(graphics);
    
    store.setReferenceSpotBoost(1.0);
    const finalBoost = store.commonSpotBoost;
    const drawRx = rx * finalBoost;
    const drawRy = ry * finalBoost;
    
    console.log('Drawing spots', spots.length, 'bounds:', minX, maxX, minY, maxY);

    spots.forEach(spot => {
        const isClassVisible = store.referenceClassFilter.includes(spot.class);
        let isForegroundVisible = true;
        if (store.referenceForegroundMode === 'foreground') isForegroundVisible = spot.foreground;
        if (store.referenceForegroundMode === 'background') isForegroundVisible = !spot.foreground;

        if (!isClassVisible || !isForegroundVisible) return;

        const color = getReferenceColor(spot.class);
        graphics.rect(spot.spatial[0] - drawRx/2, spot.spatial[1] - drawRy/2, drawRx, drawRy);
        graphics.fill(color);
    });
    if (app && app.renderer) app.render();
  }
};

// Watchers
watch(() => [store.globalZoom, store.viewOffset], () => {
    updateViewTransform();
    if (app && app.renderer) app.render();
});

watch(() => [store.referenceData, store.referenceMeta], () => {
    updateContent();
    if (app && app.renderer) app.render();
});

// For spot appearance changes, we need to redraw spots but keep transform
watch(() => [store.referenceClassFilter, store.commonSpotBoost, store.referenceSpotSize, store.referenceForegroundMode], async () => {
    await updateContent();
    if (app && app.renderer) app.render();
}, { deep: true });

onMounted(() => {
  initPixi();
  if (canvasContainer.value) {
    resizeObserver = new ResizeObserver(() => {
        // app.resize() is handled by resizeTo option
        if (app && app.renderer) {
            updateViewTransform();
            updateContent();
            app.render();
        }
    });
    resizeObserver.observe(canvasContainer.value);
  }
});

onUnmounted(() => {
  resizeObserver?.disconnect();
  if (app) {
    try {
        app.destroy(true, { children: true, texture: true });
    } catch (e) { console.error('Error destroying app in unmount', e); }
    app = null;
  }
});
</script>

<template>
  <div ref="canvasContainer" class="absolute inset-0 w-full h-full pointer-events-none"></div>
</template>
