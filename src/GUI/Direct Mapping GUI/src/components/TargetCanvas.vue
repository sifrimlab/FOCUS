<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { useMainStore } from '../store/main';
import { mat3 } from 'gl-matrix';
import { createIdentity, scale, translate, multiply, rotate } from '../utils/matrix';
import { getTargetColor } from '../utils/colors';
import type { SpotModalityPayload } from '../api/types';

const canvas = ref<HTMLCanvasElement | null>(null);
const store = useMainStore();
let resizeObserver: ResizeObserver | null = null;
let isDragging = false;
let lastX = 0;
let lastY = 0;

const calculateFitMatrix = (width: number, height: number) => {
  // Use Base Dimensions
  width = width / store.globalZoom;
  height = height / store.globalZoom;

  const m = createIdentity();
  if (!store.targetData || !store.targetMeta) return m;

  if (store.targetMeta.modality_type === 'IMAGE') {
    if (store.targetMeta.image_shape) {
        const [h, w] = store.targetMeta.image_shape;
        const scaleX = width / w;
        const scaleY = height / h;
        const s = Math.min(scaleX, scaleY);
        const dx = (width - w * s) / 2;
        const dy = (height - h * s) / 2;
        translate(m, m, [dx, dy]);
        scale(m, m, [s, s]);
    }
  } else {
    const data = store.targetData as SpotModalityPayload;
    const spots = data;
    const rasterSize = store.targetMeta!.raster_size!;
    
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const s of spots) {
      minX = Math.min(minX, s.spatial[0]);
      maxX = Math.max(maxX, s.spatial[0]);
      minY = Math.min(minY, s.spatial[1]);
      maxY = Math.max(maxY, s.spatial[1]);
    }
    const rx = rasterSize[0];
    const ry = rasterSize[1];
    minX -= rx/2; maxX += rx/2;
    minY -= ry/2; maxY += ry/2;
    
    const contentW = maxX - minX;
    const contentH = maxY - minY;
    
    const scaleX = width / contentW;
    const scaleY = height / contentH;
    const s = Math.min(scaleX, scaleY) * 0.9;
    
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    
    translate(m, m, [width/2, height/2]);
    scale(m, m, [s, -s]);
    translate(m, m, [-cx, -cy]);
  }
  return m;
};

const getLocalCenter = () => {
  if (!store.targetMeta) return [0, 0];
  if (store.targetMeta.modality_type === 'IMAGE') {
     const [h, w] = store.targetMeta.image_shape || [0, 0];
     return [w/2, h/2];
  } else {
     const data = store.targetData as SpotModalityPayload;
     if (!data) return [0, 0];
     let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
     for (const s of data) {
        minX = Math.min(minX, s.spatial[0]);
        maxX = Math.max(maxX, s.spatial[0]);
        minY = Math.min(minY, s.spatial[1]);
        maxY = Math.max(maxY, s.spatial[1]);
     }
     if (minX === Infinity) return [0, 0];
     return [(minX + maxX) / 2, (minY + maxY) / 2];
  }
};

watch(() => store.pendingCommand, (cmd) => {
  if (!cmd || !canvas.value) return;
  const { width, height } = canvas.value.getBoundingClientRect();
  
  // Operations should happen in Base Space (unzoomed)
  // Center of Base Space (Screen Center)
  const cx_screen = (width / store.globalZoom) / 2;
  const cy_screen = (height / store.globalZoom) / 2;
  
  // Target Center in Base Space
  const localCenter = getLocalCenter();
  const mCurrent = store.targetTransform;
  const cx_target = (localCenter[0] || 0) * mCurrent[0] + (localCenter[1] || 0) * mCurrent[3] + mCurrent[6];
  const cy_target = (localCenter[0] || 0) * mCurrent[1] + (localCenter[1] || 0) * mCurrent[4] + mCurrent[7];

  const m = mat3.create();
  
  if (cmd.type === 'reset') {
     // Global Reset: Identity (Scale 1, Rot 0, Trans 0)
     store.updateTargetTransform(createIdentity());
  } else if (cmd.type === 'zoom') {
     // Zoom is a view operation, usually around screen center?
     // "Zoom controller (reference) Purely visual" -> Handled by globalZoom.
     // Wait, is this 'zoom' command used? 
     // The slider maps to store.globalZoom directly.
     // The 'zoom' command might be legacy or unused?
     // Let's check ControlPanel.
     // ControlPanel uses v-model.number="store.globalZoom".
     // So 'zoom' command is likely not used.
     // But if it were, it should probably be around screen center.
     translate(m, m, [cx_screen, cy_screen]);
     scale(m, m, [cmd.value, cmd.value]);
     translate(m, m, [-cx_screen, -cy_screen]);
     const newM = mat3.create();
     multiply(newM, m, store.targetTransform);
     store.updateTargetTransform(newM);
  } else if (cmd.type === 'rotate') {
     // Rotate around Target Center
     translate(m, m, [cx_target, cy_target]);
     rotate(m, m, cmd.value);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, store.targetTransform);
     store.updateTargetTransform(newM);
  } else if (cmd.type === 'flip') {
     // Flip around Target Center
     translate(m, m, [cx_target, cy_target]);
     scale(m, m, cmd.value ? [-1, 1] : [1, -1]);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, store.targetTransform);
     store.updateTargetTransform(newM);
  } else if (cmd.type === 'setScale') {
     // Scale around Target Center
     const currentM = store.targetTransform;
     const currentScale = Math.hypot(currentM[0], currentM[1]);
     const targetScale = cmd.value;
     if (currentScale !== 0) {
        const ratio = targetScale / currentScale;
        translate(m, m, [cx_target, cy_target]);
        scale(m, m, [ratio, ratio]);
        translate(m, m, [-cx_target, -cy_target]);
        const newM = mat3.create();
        multiply(newM, m, store.targetTransform);
        store.updateTargetTransform(newM);
     }
  } else if (cmd.type === 'setRotation') {
     // Rotate around Target Center
     const currentM = store.targetTransform;
     const currentRot = Math.atan2(currentM[1], currentM[0]);
     const targetRot = cmd.value * Math.PI / 180; // degrees to radians
     const delta = targetRot - currentRot;
     
     translate(m, m, [cx_target, cy_target]);
     rotate(m, m, delta);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, store.targetTransform);
     store.updateTargetTransform(newM);
  } else if (cmd.type === 'resetScale') {
     // Reset scale to initial scale from metadata (or 1.0)
     // Preserve Rotation and Translation (Center position)
     const initialScale = store.targetMeta?.scaling_factor || 1.0;
     
     const currentM = store.targetTransform;
     const currentScale = Math.hypot(currentM[0], currentM[1]);
     
     if (currentScale !== 0) {
        const ratio = initialScale / currentScale;
        translate(m, m, [cx_target, cy_target]);
        scale(m, m, [ratio, ratio]);
        translate(m, m, [-cx_target, -cy_target]);
        const newM = mat3.create();
        multiply(newM, m, store.targetTransform);
        store.updateTargetTransform(newM);
     }
  } else if (cmd.type === 'resetRotation') {
     // Reset rotation to 0 and flip to None. Preserve Scale and Center Position.
     const currentM = store.targetTransform;
     const s = Math.hypot(currentM[0], currentM[1]); // Extract scale magnitude
     
     // We want M_new such that:
     // 1. Scale is s
     // 2. Rotation is 0
     // 3. M_new * localCenter = cx_target, cy_target
     
     // M_new = [s, 0, 0, s, tx, ty]
     // x_base = s * x_local + tx
     // y_base = s * y_local + ty
     // tx = x_base - s * x_local
     // ty = y_base - s * y_local
     
     const tx = cx_target - s * (localCenter[0] || 0);
     const ty = cy_target - s * (localCenter[1] || 0);
     
     const newM = mat3.fromValues(
         s, 0, 0,
         0, s, 0,
         tx, ty, 1
     );
     store.updateTargetTransform(newM);
  }
  
  store.pendingCommand = null;
});

const render = () => {
  if (!canvas.value || !store.targetData || !store.targetMeta) return;
  const ctx = canvas.value.getContext('2d');
  if (!ctx) return;

  const { width, height } = canvas.value.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.value.width = width * dpr;
  canvas.value.height = height * dpr;
  ctx.scale(dpr, dpr);

  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.value.width, canvas.value.height);
  ctx.restore();

  // Apply View Transform (Zoom + Pan)
  const cx = width / 2;
  const cy = height / 2;
  ctx.translate(cx, cy);
  ctx.scale(store.globalZoom, store.globalZoom);
  ctx.translate(store.viewOffset[0], store.viewOffset[1]);
  ctx.translate(-cx, -cy);

  ctx.globalAlpha = store.targetOpacity;

  const m = store.targetTransform;

  if (store.targetMeta.modality_type === 'IMAGE') {
    const imgBlob = store.targetData as Blob;
    const img = new Image();
    img.src = URL.createObjectURL(imgBlob);
    img.onload = () => {
        ctx.save();
        ctx.transform(m[0], m[1], m[3], m[4], m[6], m[7]);
        ctx.drawImage(img, 0, 0);
        ctx.restore();
    };
  } else {
    const data = store.targetData as SpotModalityPayload;
    if (!data || !store.targetMeta?.raster_size) return;
    const spots = data;
    const rasterSize = store.targetMeta.raster_size;
    const rx = rasterSize[0];
    const ry = rasterSize[1];

    ctx.save();
    ctx.transform(m[0], m[1], m[3], m[4], m[6], m[7]);

    spots.forEach(spot => {
        // Target spots don't have filter? "Class filter (spot only)" in Common Elements.
        // Assuming it applies to both or just Reference?
        // "Class filter (spot only) ... toggles visibility (opacity) of spots per class".
        // If Target is Spot, it should probably apply too.
        const isVisible = store.targetClassFilter.includes(spot.class);
        ctx.globalAlpha = (isVisible ? 1 : 0.1) * store.targetOpacity;
        
        if (!isVisible) return;

        ctx.fillStyle = getTargetColor(spot.class);
        ctx.fillRect(spot.spatial[0] - rx/2, spot.spatial[1] - ry/2, rx, ry);
    });
    ctx.restore();
  }
};

// Initialize transform on data load
watch(() => store.targetData, () => {
    if (canvas.value && store.targetData) {
        const { width, height } = canvas.value.getBoundingClientRect();
        const fitM = calculateFitMatrix(width, height);
        store.updateTargetTransform(fitM);
    }
});

watch(() => [store.targetTransform, store.targetOpacity, store.targetClassFilter, store.globalZoom, store.viewOffset], render, { deep: true });

const onMouseDown = (e: MouseEvent) => {
  isDragging = true;
  lastX = e.clientX;
  lastY = e.clientY;
};

const onMouseMove = (e: MouseEvent) => {
  if (!isDragging) return;
  const dx = (e.clientX - lastX) / store.globalZoom;
  const dy = (e.clientY - lastY) / store.globalZoom;
  lastX = e.clientX;
  lastY = e.clientY;
  
  const m = mat3.create();
  translate(m, m, [dx, dy]);
  
  const newM = mat3.create();
  multiply(newM, m, store.targetTransform);
  store.updateTargetTransform(newM);
};

const onMouseUp = () => {
  isDragging = false;
};

const onWheel = (e: WheelEvent) => {
  e.preventDefault();
  const zoom = e.deltaY > 0 ? 0.98 : 1.02; // Slower sensitivity
  
  const rect = canvas.value!.getBoundingClientRect();
  const mx_screen = e.clientX - rect.left;
  const my_screen = e.clientY - rect.top;
  
  // Map to Base Space
  // Inverse View Transform:
  // 1. Translate(-cx, -cy)
  // 2. Translate(-panX, -panY)
  // 3. Scale(1/zoom)
  // 4. Translate(cx, cy)
  
  const cx = rect.width / 2;
  const cy = rect.height / 2;
  
  const mx = (mx_screen - cx - store.viewOffset[0] * store.globalZoom) / store.globalZoom + cx;
  const my = (my_screen - cy - store.viewOffset[1] * store.globalZoom) / store.globalZoom + cy;
  
  const m = mat3.create();
  translate(m, m, [mx, my]);
  scale(m, m, [zoom, zoom]);
  translate(m, m, [-mx, -my]);
  
  const newM = mat3.create();
  multiply(newM, m, store.targetTransform);
  store.updateTargetTransform(newM);
};

onMounted(() => {
  if (canvas.value) {
    resizeObserver = new ResizeObserver(() => {
        // On resize, we might want to re-fit? Or keep transform?
        // Usually keep transform relative to center?
        // For now just re-render.
        render();
    });
    resizeObserver.observe(canvas.value.parentElement!);
    
    window.addEventListener('mouseup', onMouseUp);
  }
});

onUnmounted(() => {
  resizeObserver?.disconnect();
  window.removeEventListener('mouseup', onMouseUp);
});
</script>

<template>
  <canvas 
    ref="canvas" 
    class="absolute inset-0 w-full h-full cursor-move"
    @mousedown="onMouseDown"
    @mousemove="onMouseMove"
    @wheel="onWheel"
  ></canvas>
</template>
