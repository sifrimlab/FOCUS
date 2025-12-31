<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { useMainStore } from '../store/main';
import { createIdentity, scale, translate } from '../utils/matrix';
import { getReferenceColor } from '../utils/colors';
import type { SpotModalityPayload } from '../api/types';

const canvas = ref<HTMLCanvasElement | null>(null);
const store = useMainStore();
let resizeObserver: ResizeObserver | null = null;

const render = () => {
  if (!canvas.value || !store.referenceData || !store.referenceMeta) return;
  const ctx = canvas.value.getContext('2d');
  if (!ctx) return;

  const { width, height } = canvas.value.getBoundingClientRect();
  // Handle high DPI
  const dpr = window.devicePixelRatio || 1;
  canvas.value.width = width * dpr;
  canvas.value.height = height * dpr;
  ctx.scale(dpr, dpr);

  // Apply View Transform (Zoom + Pan)
  const cx = width / 2;
  const cy = height / 2;
  ctx.translate(cx, cy);
  ctx.scale(store.globalZoom, store.globalZoom);
  ctx.translate(store.viewOffset[0], store.viewOffset[1]);
  ctx.translate(-cx, -cy);

  const mRef = createIdentity();

  if (store.referenceMeta.modality_type === 'IMAGE') {
    const imgBlob = store.referenceData as Blob;
    const img = new Image();
    img.src = URL.createObjectURL(imgBlob);
    img.onload = () => {
      const imgW = img.width;
      const imgH = img.height;
      
      // Calculate Fit Matrix (Base Transform)
      // This should be constant regardless of zoom/pan
      const scaleX = width / imgW;
      const scaleY = height / imgH;
      const s = Math.min(scaleX, scaleY);
      
      const dx = (width - imgW * s) / 2;
      const dy = (height - imgH * s) / 2;
      
      // M_ref: Image Space -> Base Screen Space
      translate(mRef, mRef, [dx, dy]);
      scale(mRef, mRef, [s, s]);
      
      // Draw using Base Transform (View Transform is already applied to Context)
      ctx.drawImage(img, dx, dy, imgW * s, imgH * s);
      
      store.updateReferenceTransform(mRef);
    };
  } else if (store.referenceMeta.modality_type === 'SPOT') {
    const data = store.referenceData as SpotModalityPayload;
    if (!data || !store.referenceMeta?.raster_size) return;
    const spots = data;
    const rasterSize = store.referenceMeta.raster_size;
    
    // Calculate bounds
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const s of spots) {
      minX = Math.min(minX, s.spatial[0]);
      maxX = Math.max(maxX, s.spatial[0]);
      minY = Math.min(minY, s.spatial[1]);
      maxY = Math.max(maxY, s.spatial[1]);
    }
    
    // Add raster size to bounds?
    // Spots are centered at spatial.
    const rx = rasterSize[0];
    const ry = rasterSize[1];
    minX -= rx/2; maxX += rx/2;
    minY -= ry/2; maxY += ry/2;
    
    const contentW = maxX - minX;
    const contentH = maxY - minY;
    
    // Scale to fit
    const scaleX = width / contentW;
    const scaleY = height / contentH;
    const s = Math.min(scaleX, scaleY) * 0.9; // 90% fill
    
    // Center
    const dx = (width - contentW * s) / 2;
    const dy = (height - contentH * s) / 2;
    
    // M_ref: Spot Space -> Screen Space
    // We need to map (minX, minY) to (dx, dy) ?
    // No, we map (0,0) to somewhere.
    // ScreenX = (SpotX - minX) * s + dx
    // ScreenX = SpotX * s - minX * s + dx
    // Translate(dx - minX*s, dy - minY*s) * Scale(s)
    
    translate(mRef, mRef, [dx - minX * s, dy - minY * s]);
    scale(mRef, mRef, [s, s]); // Y is up in Spot space? "origin = (0,0) bottom‑left".
    // Canvas Y is down.
    // So we need to flip Y.
    // ScreenY = height - ((SpotY - minY) * s + dy) ? No.
    // Usually we want to map Spot Y (up) to Screen Y (down).
    // Let's assume standard Cartesian to Screen mapping.
    // Scale(s, -s).
    // And translate to center.
    
    // Let's re-calculate for Y-flip.
    // Center of content in Spot Space: cx = (minX+maxX)/2, cy = (minY+maxY)/2.
    // Center of Screen: W/2, H/2.
    // M: Translate(W/2, H/2) * Scale(s, -s) * Translate(-cx, -cy).
    
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    
    translate(mRef, mRef, [width/2, height/2]);
    scale(mRef, mRef, [s, -s]);
    translate(mRef, mRef, [-cx, -cy]);
    
    store.updateReferenceTransform(mRef);
    
    // Draw spots
    // We can use the matrix to transform context?
    // Yes.
    ctx.save();
    // Apply matrix
    // gl-matrix is column-major. Context is row-major?
    // ctx.setTransform(a, b, c, d, e, f)
    // mat3: [a, b, 0, c, d, 0, e, f, 1]
    // ctx: a, b, c, d, e, f (where c is skewX, b is skewY... wait)
    // ctx.transform(m11, m12, m21, m22, dx, dy)
    // mat3 indices:
    // 0: m11, 1: m12, 2: 0
    // 3: m21, 4: m22, 5: 0
    // 6: dx,  7: dy,  8: 1
    ctx.transform(mRef[0], mRef[1], mRef[3], mRef[4], mRef[6], mRef[7]);
    
    spots.forEach(spot => {
        // Filter?
        const isVisible = store.referenceClassFilter.includes(spot.class);
        ctx.globalAlpha = isVisible ? 1 : 0.1; // Or 0 if completely hidden
        if (!isVisible) return; // Optimization: skip drawing if hidden

        ctx.fillStyle = getReferenceColor(spot.class);
        // Draw rect centered at spatial
        ctx.fillRect(spot.spatial[0] - rx/2, spot.spatial[1] - ry/2, rx, ry);
    });
    
    ctx.restore();
  }
};

watch(() => [store.referenceData, store.referenceClassFilter, store.globalZoom, store.viewOffset], render, { deep: true });

onMounted(() => {
  if (canvas.value) {
    resizeObserver = new ResizeObserver(render);
    resizeObserver.observe(canvas.value.parentElement!); // Observe container
  }
});

onUnmounted(() => {
  resizeObserver?.disconnect();
});
</script>

<template>
  <canvas ref="canvas" class="absolute inset-0 w-full h-full pointer-events-none"></canvas>
</template>
