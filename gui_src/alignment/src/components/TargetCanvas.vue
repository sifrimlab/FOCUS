<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { Application, Container, Sprite, Graphics, Texture } from 'pixi.js';
import { useMainStore } from '../store/main';
import { mat3 } from 'gl-matrix';
import { createIdentity, scale, translate, multiply, rotate } from '../utils/matrix';
import { getTargetColor } from '../utils/colors';
import type { SpotModalityPayload } from '../api/types';

const canvasContainer = ref<HTMLElement | null>(null);
const store = useMainStore();
let app: Application | null = null;
let viewContainer: Container | null = null;
let contentContainer: Container | null = null;
let resizeObserver: ResizeObserver | null = null;
let isDragging = false;
let lastX = 0;
let lastY = 0;
let cachedLocalCenter: [number, number] | null = null;
let cachedLocalBBox: { minX: number; minY: number; maxX: number; maxY: number } | null = null;
let overlayGraphics: Graphics | null = null;
let activeHandleIndex = -1;
let distortAxisLock: 'x' | 'y' | null = null;
let distortAccumX = 0;
let distortAccumY = 0;
const AXIS_LOCK_THRESHOLD = 5;
const HANDLE_HIT_RADIUS = 15;

const calculateFitMatrix = (width: number, height: number) => {
  // Use Base Dimensions
  width = width / store.globalZoom;
  height = height / store.globalZoom;

  const m = createIdentity();
  if (!store.targetData || !store.targetMeta) return m;

  if (store.targetMeta.modality_type === 'IMAGE') {
    if (store.targetMeta.image_shape) {
        const [h, w] = store.targetMeta.image_shape;
        const s = 1.0;
        const dx = (width - w * s) / 2;
        const dy = (height - h * s) / 2;
        translate(m, m, [dx, dy]);
        scale(m, m, [s, s]);
    }
  } else {
    const data = store.targetData as SpotModalityPayload;
    const spots = data;
    const spotSize = store.targetMeta!.spot_size!;
    
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
    
    translate(m, m, [width/2, height/2]);
    scale(m, m, [s, -s]);
    translate(m, m, [-cx, -cy]);
  }
  return m;
};

const getLocalCenter = () => {
  if (cachedLocalCenter) return cachedLocalCenter;
  if (!store.targetMeta) return [0, 0];
  if (store.targetMeta.modality_type === 'IMAGE') {
     const [h, w] = store.targetMeta.image_shape || [0, 0];
     cachedLocalCenter = [w/2, h/2];
     return cachedLocalCenter;
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
     cachedLocalCenter = [(minX + maxX) / 2, (minY + maxY) / 2];
     return cachedLocalCenter;
  }
};

const getTargetBBox = () => {
  if (cachedLocalBBox) return cachedLocalBBox;
  if (!store.targetMeta) return null;
  if (store.targetMeta.modality_type === 'IMAGE') {
    const [h, w] = store.targetMeta.image_shape || [0, 0];
    cachedLocalBBox = { minX: 0, minY: 0, maxX: w, maxY: h };
  } else {
    const data = store.targetData as SpotModalityPayload;
    if (!data) return null;
    const [rx, ry] = store.targetSpotSize;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const s of data) {
      minX = Math.min(minX, s.spatial[0]); maxX = Math.max(maxX, s.spatial[0]);
      minY = Math.min(minY, s.spatial[1]); maxY = Math.max(maxY, s.spatial[1]);
    }
    cachedLocalBBox = { minX: minX - rx/2, minY: minY - ry/2, maxX: maxX + rx/2, maxY: maxY + ry/2 };
  }
  return cachedLocalBBox;
};

const worldToScreen = (wx: number, wy: number): [number, number] => {
  if (!app) return [0, 0];
  const { width, height } = app.screen;
  const cx = width / 2; const cy = height / 2;
  return [
    (wx - (cx - store.viewOffset[0])) * store.globalZoom + cx,
    (wy - (cy - store.viewOffset[1])) * store.globalZoom + cy,
  ];
};

const getHandleScreenPositions = (): [number, number][] => {
  const bbox = getTargetBBox();
  if (!bbox) return [];
  const m = store.targetTransform;
  const localCorners: [number, number][] = [
    [bbox.minX, bbox.minY], [bbox.maxX, bbox.minY],
    [bbox.maxX, bbox.maxY], [bbox.minX, bbox.maxY],
  ];
  return localCorners.map(([lx, ly]) => {
    const wx = lx * m[0] + ly * m[3] + m[6];
    const wy = lx * m[1] + ly * m[4] + m[7];
    return worldToScreen(wx, wy);
  });
};

const drawDistortOverlay = () => {
  if (!overlayGraphics) return;
  overlayGraphics.clear();
  if (store.controlMode !== 'aligner' || store.alignerInteraction !== 'distort') return;

  const handles = getHandleScreenPositions();
  if (handles.length !== 4) return;
  const [h0, h1, h2, h3] = handles as [[number,number],[number,number],[number,number],[number,number]];

  overlayGraphics.setStrokeStyle({ width: 1.5, color: 0xFFD700, alpha: 0.9 });
  overlayGraphics.moveTo(h0[0], h0[1]);
  overlayGraphics.lineTo(h1[0], h1[1]);
  overlayGraphics.lineTo(h2[0], h2[1]);
  overlayGraphics.lineTo(h3[0], h3[1]);
  overlayGraphics.lineTo(h0[0], h0[1]);
  overlayGraphics.stroke();

  [h0, h1, h2, h3].forEach(([hx, hy], i) => {
    const isActive = activeHandleIndex === i;
    overlayGraphics!.circle(hx, hy, isActive ? 8 : 6);
    overlayGraphics!.fill({ color: isActive ? 0xFFFFFF : 0xFFD700, alpha: 0.9 });
  });
};

// Reset cache when data changes
watch(() => store.targetData, () => {
    cachedLocalCenter = null;
    cachedLocalBBox = null;
    if (app && store.targetData) {
        const { width, height } = app.screen;
        const fitM = calculateFitMatrix(width, height);
        store.updateTargetTransform(fitM);
        updateContent();
    }
});

const initPixi = async () => {
  if (!canvasContainer.value) return;
  
  if (app) {
      try {
        app.destroy(true, { children: true, texture: true });
      } catch (e) { console.error(e); }
      app = null;
  }
  
  const newApp = new Application();
  try {
      await newApp.init({ 
        resizeTo: canvasContainer.value, 
        backgroundAlpha: 0,
        antialias: true,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
        preference: 'webgl',
        autoStart: false
      });
  } catch (e) { return; }
  
  if (!canvasContainer.value) {
      newApp.destroy(true);
      return;
  }
  
  app = newApp;
  canvasContainer.value.appendChild(app.canvas);
  app.canvas.style.width = '100%';
  app.canvas.style.height = '100%';
  app.canvas.style.display = 'block';
  
  // Attach event listeners
  app.canvas.addEventListener('mousedown', onMouseDown);
  app.canvas.addEventListener('wheel', onWheel);
  
  viewContainer = new Container();
  app.stage.addChild(viewContainer);

  contentContainer = new Container();
  viewContainer.addChild(contentContainer);

  overlayGraphics = new Graphics();
  app.stage.addChild(overlayGraphics);
  
  render();
  
  setTimeout(() => {
      if (app && app.renderer) {
          app.resize();
          updateViewTransform();
          updateContent();
      }
  }, 100);
};

const updateViewTransform = () => {
  if (!viewContainer || !app) return;
  const { width, height } = app.screen;
  const cx = width / 2;
  const cy = height / 2;
  
  viewContainer.position.set(cx, cy);
  viewContainer.scale.set(store.globalZoom);
  viewContainer.pivot.set(cx - store.viewOffset[0], cy - store.viewOffset[1]);
};

const updateContentTransform = () => {
    if (!contentContainer || !store.targetTransform) return;
    const m = store.targetTransform;

    const a = m[0]; const b = m[1]; const c = m[3]; const d = m[4];
    const tx = m[6]; const ty = m[7];

    // Pixi v8 formula: a = cos(rot+skewY)*scaleX, b = sin(rot+skewY)*scaleX
    //                  c = -sin(rot-skewX)*scaleY, d = cos(rot-skewX)*scaleY
    // Setting skewY=0 → rotation=atan2(b,a), skewX=atan2(b,a)-atan2(-c,d)
    const rotationX = Math.atan2(b, a);
    const rotationY = Math.atan2(-c, d);

    contentContainer.position.set(tx, ty);
    contentContainer.rotation = rotationX;
    contentContainer.skew.x = rotationX - rotationY;
    contentContainer.skew.y = 0;
    contentContainer.scale.set(Math.sqrt(a * a + b * b), Math.sqrt(c * c + d * d));
    contentContainer.alpha = store.targetOpacity;
};

const updateContent = async () => {
    if (!app || !contentContainer) return;
    contentContainer.removeChildren();
    
    if (!store.targetData || !store.targetMeta) return;
    
    if (store.targetMeta.modality_type === 'IMAGE') {
        const imgBlob = store.targetData as Blob;
        const url = URL.createObjectURL(imgBlob);
        try {
            const img = new Image();
            img.src = url;
            await img.decode();
            URL.revokeObjectURL(url);
            const texture = Texture.from(img);
            const sprite = new Sprite(texture);
            contentContainer.addChild(sprite);
            if (app && app.renderer) app.render();
        } catch (e) { console.error(e); }
    } else {
        const data = store.targetData as SpotModalityPayload;
        const spots = data;
        const spotSize = store.targetSpotSize;
        const rx = spotSize[0];
        const ry = spotSize[1];
        
        store.setTargetSpotBoost(1.0);
        const finalBoost = store.commonSpotBoost;
        const drawRx = rx * finalBoost;
        const drawRy = ry * finalBoost;
        
        const graphics = new Graphics();
        contentContainer.addChild(graphics);
        
        spots.forEach(spot => {
            const isClassVisible = store.targetClassFilter.includes(spot.class);
            let isForegroundVisible = true;
            if (store.targetForegroundMode === 'foreground') isForegroundVisible = spot.foreground;
            if (store.targetForegroundMode === 'background') isForegroundVisible = !spot.foreground;
            
            const isVisible = isClassVisible && isForegroundVisible;
            if (!isVisible) return;

            const color = getTargetColor(spot.class);
            graphics.rect(spot.spatial[0] - drawRx/2, spot.spatial[1] - drawRy/2, drawRx, drawRy);
            graphics.fill(color);
        });
    }
    updateContentTransform();
};

const render = () => {
    if (!app || !app.renderer) return;
    updateViewTransform();
    updateContentTransform();
    app.render();
};

watch(() => store.pendingCommand, (cmd) => {
  if (!cmd || !app) return;
  const { width, height } = app.screen;
  
  const cx_screen = (width / store.globalZoom) / 2;
  const cy_screen = (height / store.globalZoom) / 2;
  
  const localCenter = getLocalCenter();
  const mCurrent = store.targetTransform;
  const cx_target = (localCenter[0] || 0) * mCurrent[0] + (localCenter[1] || 0) * mCurrent[3] + mCurrent[6];
  const cy_target = (localCenter[0] || 0) * mCurrent[1] + (localCenter[1] || 0) * mCurrent[4] + mCurrent[7];

  const m = mat3.create();
  
  if (cmd.type === 'reset') {
     store.updateTargetTransform(createIdentity());
  } else if (cmd.type === 'zoom') {
     translate(m, m, [cx_screen, cy_screen]);
     scale(m, m, [cmd.value, cmd.value]);
     translate(m, m, [-cx_screen, -cy_screen]);
     const newM = mat3.create();
     multiply(newM, m, store.targetTransform);
     store.updateTargetTransform(newM);
  } else if (cmd.type === 'rotate') {
     translate(m, m, [cx_target, cy_target]);
     rotate(m, m, cmd.value);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, store.targetTransform);
     store.updateTargetTransform(newM);
  } else if (cmd.type === 'flip') {
     translate(m, m, [cx_target, cy_target]);
     scale(m, m, cmd.value ? [-1, 1] : [1, -1]);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, store.targetTransform);
     store.updateTargetTransform(newM);
  } else if (cmd.type === 'setScale') {
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
     const currentM = store.targetTransform;
     const currentRot = Math.atan2(currentM[1], currentM[0]);
     const targetRot = cmd.value * Math.PI / 180;
     const delta = targetRot - currentRot;
     
     translate(m, m, [cx_target, cy_target]);
     rotate(m, m, delta);
     translate(m, m, [-cx_target, -cy_target]);
     const newM = mat3.create();
     multiply(newM, m, store.targetTransform);
     store.updateTargetTransform(newM);
  } else if (cmd.type === 'resetScale') {
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
     const currentM = store.targetTransform;
     const s = Math.hypot(currentM[0], currentM[1]);
     const tx = cx_target - s * (localCenter[0] || 0);
     const ty = cy_target - s * (localCenter[1] || 0);
     const newM = mat3.fromValues(s, 0, 0, 0, s, 0, tx, ty, 1);
     store.updateTargetTransform(newM);
  }
  store.pendingCommand = null;
});

// Initialize transform on data load
// watch(() => store.targetData, () => { ... }) // Moved above to handle cache reset


watch(() => [store.targetTransform, store.targetOpacity], () => {
    updateContentTransform();
    render();
}, { deep: true });

watch(() => [store.globalZoom, store.viewOffset], () => {
    updateViewTransform();
    render();
}, { deep: true });

watch(() => [store.targetClassFilter, store.commonSpotBoost, store.targetSpotSize, store.targetForegroundMode], async () => {
    await updateContent();
    render();
}, { deep: true });

watch(
  () => [store.targetTransform, store.alignerInteraction, store.globalZoom, store.viewOffset, store.controlMode],
  () => { drawDistortOverlay(); },
  { deep: true }
);

const onMouseDown = (e: MouseEvent) => {
  if (store.controlMode === 'aligner' && store.alignerInteraction === 'distort' && app) {
    const rect = app.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const handles = getHandleScreenPositions();
    for (let i = 0; i < handles.length; i++) {
      const h = handles[i]!;
      if (Math.hypot(mx - h[0], my - h[1]) < HANDLE_HIT_RADIUS) {
        activeHandleIndex = i;
        distortAxisLock = null;
        distortAccumX = 0;
        distortAccumY = 0;
        isDragging = true;
        lastX = e.clientX;
        lastY = e.clientY;
        return;
      }
    }
    return;
  }
  isDragging = true;
  lastX = e.clientX;
  lastY = e.clientY;
};

const onMouseMove = (e: MouseEvent) => {
  if (!isDragging) return;

  if (store.alignerInteraction === 'distort' && activeHandleIndex >= 0) {
    const dx_screen = e.clientX - lastX;
    const dy_screen = e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;

    if (distortAxisLock === null) {
      distortAccumX += dx_screen;
      distortAccumY += dy_screen;
      if (Math.abs(distortAccumX) >= AXIS_LOCK_THRESHOLD || Math.abs(distortAccumY) >= AXIS_LOCK_THRESHOLD) {
        distortAxisLock = Math.abs(distortAccumX) >= Math.abs(distortAccumY) ? 'x' : 'y';
      }
      return;
    }

    const handles = getHandleScreenPositions();
    if (handles.length !== 4) return;
    const [sh0, sh1, , sh3] = handles as [[number,number],[number,number],[number,number],[number,number]];
    const bboxScreenWidth = Math.hypot(sh1[0] - sh0[0], sh1[1] - sh0[1]);
    const bboxScreenHeight = Math.hypot(sh3[0] - sh0[0], sh3[1] - sh0[1]);

    const localCenter = getLocalCenter();
    const mCur = store.targetTransform;
    const cx_w = (localCenter[0] || 0) * mCur[0] + (localCenter[1] || 0) * mCur[3] + mCur[6];
    const cy_w = (localCenter[0] || 0) * mCur[1] + (localCenter[1] || 0) * mCur[4] + mCur[7];

    const shearM = mat3.create();
    if (distortAxisLock === 'x' && bboxScreenHeight > 0) {
      shearM[3] = dx_screen / (bboxScreenHeight / 2);
    } else if (distortAxisLock === 'y' && bboxScreenWidth > 0) {
      shearM[1] = dy_screen / (bboxScreenWidth / 2);
    }

    const mT = mat3.create();
    translate(mT, mT, [cx_w, cy_w]);
    const mTi = mat3.create();
    translate(mTi, mTi, [-cx_w, -cy_w]);

    const fullShear = mat3.create();
    multiply(fullShear, shearM, mTi);
    multiply(fullShear, mT, fullShear);

    const newM = mat3.create();
    multiply(newM, fullShear, store.targetTransform);
    store.updateTargetTransform(newM);
    return;
  }

  if (store.controlMode === 'camera') {
      const screenDx = e.clientX - lastX;
      const screenDy = e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      store.updateViewOffset(screenDx, screenDy);
      return;
  }

  // Aligner Mode
  if (store.alignerInteraction === 'rotate') {
      if (!app) return;
      const rect = app.canvas.getBoundingClientRect();
      const cx_screen = rect.width / 2;
      const cy_screen = rect.height / 2;

      const localCenter = getLocalCenter();
      const mCurrent = store.targetTransform;
      
      const cx_target_world = (localCenter[0] || 0) * mCurrent[0] + (localCenter[1] || 0) * mCurrent[3] + mCurrent[6];
      const cy_target_world = (localCenter[0] || 0) * mCurrent[1] + (localCenter[1] || 0) * mCurrent[4] + mCurrent[7];

      const cx_target_screen = (cx_target_world - store.viewOffset[0]) * store.globalZoom + cx_screen;
      const cy_target_screen = (cy_target_world - store.viewOffset[1]) * store.globalZoom + cy_screen;

      const mx_screen = e.clientX - rect.left;
      const my_screen = e.clientY - rect.top;
      const last_mx_screen = lastX - rect.left;
      const last_my_screen = lastY - rect.top;

      const angleOld = Math.atan2(last_my_screen - cy_target_screen, last_mx_screen - cx_target_screen);
      const angleNew = Math.atan2(my_screen - cy_target_screen, mx_screen - cx_target_screen);
      const dAngle = angleNew - angleOld;
      
      const m = mat3.create();
      translate(m, m, [cx_target_world, cy_target_world]);
      rotate(m, m, dAngle);
      translate(m, m, [-cx_target_world, -cy_target_world]);
      
      const newM = mat3.create();
      multiply(newM, m, store.targetTransform);
      store.updateTargetTransform(newM);
      
      lastX = e.clientX;
      lastY = e.clientY;
      return;
  }

  const dx = (e.clientX - lastX) / store.globalZoom;
  const dy = (e.clientY - lastY) / store.globalZoom;
  lastX = e.clientX;
  lastY = e.clientY;
  
  // We need to apply translation in the Target's Local Space?
  // No, usually we translate in the "World" space (which is View Space / Zoom).
  // But here we are modifying the Target Transform Matrix (M).
  // M maps Local -> World.
  // We want to move the object in World space by (dx, dy).
  // New_Pos = Old_Pos + Delta
  // M_new * p = M_old * p + Delta
  // M_new = Translate(Delta) * M_old
  
  const m = mat3.create();
  translate(m, m, [dx, dy]);
  
  const newM = mat3.create();
  multiply(newM, m, store.targetTransform);
  store.updateTargetTransform(newM);
};

const onMouseUp = () => {
  isDragging = false;
  activeHandleIndex = -1;
  distortAxisLock = null;
  distortAccumX = 0;
  distortAccumY = 0;
};

const onWheel = (e: WheelEvent) => {
  e.preventDefault();
  const zoom = e.deltaY > 0 ? 0.98 : 1.02;
  
  if (store.controlMode === 'camera') {
      store.globalZoom *= zoom;
      return;
  }
  
  if (!app) return;
  const rect = app.canvas.getBoundingClientRect();
  const mx_screen = e.clientX - rect.left;
  const my_screen = e.clientY - rect.top;
  
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
  initPixi();
  if (canvasContainer.value) {
    resizeObserver = new ResizeObserver(() => {
        if (app && app.renderer) {
            updateViewTransform();
            app.render();
        }
    });
    resizeObserver.observe(canvasContainer.value);
    
    window.addEventListener('mouseup', onMouseUp);
    window.addEventListener('mousemove', onMouseMove);
  }
});

onUnmounted(() => {
  resizeObserver?.disconnect();
  window.removeEventListener('mouseup', onMouseUp);
  window.removeEventListener('mousemove', onMouseMove);
  if (app) {
    try {
        app.destroy(true, { children: true, texture: true });
    } catch (e) { console.error(e); }
    app = null;
  }
});
</script>

<template>
  <div 
    ref="canvasContainer" 
    class="absolute inset-0 w-full h-full cursor-move"
  ></div>
</template>
