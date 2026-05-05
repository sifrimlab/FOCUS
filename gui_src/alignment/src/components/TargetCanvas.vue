<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { Application, Container, Sprite, Graphics, Texture } from 'pixi.js';
import { useMainStore } from '../store/main';
import { mat3 } from 'gl-matrix';
import { createIdentity, scale, translate, multiply, rotate, computeHomography, projectiveTransformPoint } from '../utils/matrix';
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
let activeHandleIndex = -1; // 0-3: corner handles, 4-7: edge handles, 8: translate, 9: rotate
let dragStartWorldCorners: [number, number][] = [];
let dragStartMouseWorld: [number, number] = [0, 0];
let transformBeforeDistort: mat3 | null = null;
let wasProjective = false;
let spotGraphics: Graphics | null = null; // reused across frames to avoid GPU alloc/free per draw
let latestHomography: mat3 | null = null; // RAF-throttled distort drag state
let distortRafId: number | null = null;
const HANDLE_HIT_RADIUS = 15;
const ROTATE_ZONE_OUTER = 30; // px — annular zone outside handle but near corner triggers rotation
// Maps each edge handle index (0-3) to the pair of corner indices it connects:
// 0=top (corners 0,1), 1=right (corners 1,2), 2=bottom (corners 2,3), 3=left (corners 3,0)
const EDGE_TO_CORNERS: [number, number][] = [[0, 1], [1, 2], [2, 3], [3, 0]];
// Rotation cursor: 270° clockwise arc (top→left) with a downward arrowhead, hotspot at center
const ROTATE_CURSOR = (() => {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">' +
    '<path d="M13 4 A9 9 0 1 1 4 13" fill="none" stroke="black" stroke-width="3" stroke-linecap="round"/>' +
    '<path d="M13 4 A9 9 0 1 1 4 13" fill="none" stroke="white" stroke-width="1.5" stroke-linecap="round"/>' +
    '<polygon points="4,17 1,11 7,11" fill="white" stroke="black" stroke-width="0.5"/>' +
    '</svg>';
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}") 12 12, auto`;
})();

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

// Properly destroy Pixi display objects so WebGL buffers are freed immediately.
// Simple removeChildren() only detaches the JS objects; without destroy() the
// GPU-side VBOs/IBOs accumulate until GC runs and WebGL context is exhausted.
const destroyChildren = () => {
  if (!contentContainer) return;
  const children = [...contentContainer.children];
  contentContainer.removeChildren();
  children.forEach(c => c.destroy({ children: true }));
  spotGraphics = null;
};

// Redraw all spots into the persistent spotGraphics object (clear + fill).
// Called every frame during projective transforms; reuses GPU buffers instead of
// allocating new ones, keeping memory usage constant regardless of drag speed.
const drawSpots = (projective: boolean) => {
  if (!spotGraphics || !store.targetData || !store.targetMeta || store.targetMeta.modality_type === 'IMAGE') return;
  const data = store.targetData as SpotModalityPayload;
  const [rx, ry] = store.targetSpotSize;
  const drawRx = rx * store.commonSpotBoost;
  const drawRy = ry * store.commonSpotBoost;
  const m = store.targetTransform;
  spotGraphics.clear();
  for (const spot of data) {
    if (!store.targetClassFilter.includes(spot.class)) continue;
    if (store.targetForegroundMode === 'foreground' && !spot.foreground) continue;
    if (store.targetForegroundMode === 'background' && spot.foreground) continue;
    const color = getTargetColor(spot.class);
    const lx = spot.spatial[0]; const ly = spot.spatial[1];
    if (projective) {
      const c0 = projectiveTransformPoint(m, lx - drawRx/2, ly - drawRy/2);
      const c1 = projectiveTransformPoint(m, lx + drawRx/2, ly - drawRy/2);
      const c2 = projectiveTransformPoint(m, lx + drawRx/2, ly + drawRy/2);
      const c3 = projectiveTransformPoint(m, lx - drawRx/2, ly + drawRy/2);
      spotGraphics.poly([c0[0], c0[1], c1[0], c1[1], c2[0], c2[1], c3[0], c3[1]]);
    } else {
      spotGraphics.rect(lx - drawRx/2, ly - drawRy/2, drawRx, drawRy);
    }
    spotGraphics.fill(color);
  }
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

const screenToWorld = (sx: number, sy: number): [number, number] => {
  if (!app) return [0, 0];
  const { width, height } = app.screen;
  const cx = width / 2; const cy = height / 2;
  return [
    (sx - cx) / store.globalZoom + cx - store.viewOffset[0],
    (sy - cy) / store.globalZoom + cy - store.viewOffset[1],
  ];
};

const isProjective = (m: mat3): boolean => Math.abs(m[2]) > 1e-10 || Math.abs(m[5]) > 1e-10;

const getHandleScreenPositions = (): [number, number][] => {
  const bbox = getTargetBBox();
  if (!bbox) return [];
  const m = store.targetTransform;
  const localCorners: [number, number][] = [
    [bbox.minX, bbox.minY], [bbox.maxX, bbox.minY],
    [bbox.maxX, bbox.maxY], [bbox.minX, bbox.maxY],
  ];
  return localCorners.map(([lx, ly]) => {
    const [wx, wy] = projectiveTransformPoint(m, lx, ly);
    return worldToScreen(wx, wy);
  });
};

const updateHoverCursor = (mx: number, my: number) => {
  if (!app) return;
  if (store.controlMode !== 'aligner') {
    app.canvas.style.cursor = '';
    return;
  }
  const cornerHandles = getHandleScreenPositions();
  for (const h of cornerHandles) {
    if (Math.hypot(mx - h[0], my - h[1]) < HANDLE_HIT_RADIUS) { app.canvas.style.cursor = 'grab'; return; }
  }
  const edgeHandles = getEdgeHandleScreenPositions();
  for (const h of edgeHandles) {
    if (Math.hypot(mx - h[0], my - h[1]) < HANDLE_HIT_RADIUS) { app.canvas.style.cursor = 'grab'; return; }
  }
  if (isInRotateZone(mx, my)) { app.canvas.style.cursor = ROTATE_CURSOR; return; }
  app.canvas.style.cursor = isInsideDistortFrame(mx, my) ? 'move' : 'default';
};

const isInsideDistortFrame = (mx: number, my: number): boolean => {
  const corners = getHandleScreenPositions();
  if (corners.length !== 4) return false;
  // Cross product sign must be consistent for all edges of the (possibly non-axis-aligned) quad
  let sign: number | null = null;
  for (let i = 0; i < 4; i++) {
    const [ax, ay] = corners[i]!;
    const [bx, by] = corners[(i + 1) % 4]!;
    const cross = (bx - ax) * (my - ay) - (by - ay) * (mx - ax);
    const s = cross > 0 ? 1 : cross < 0 ? -1 : 0;
    if (s === 0) continue;
    if (sign === null) sign = s;
    else if (sign !== s) return false;
  }
  return true;
};

const isInRotateZone = (mx: number, my: number): boolean => {
  if (isInsideDistortFrame(mx, my)) return false;
  const corners = getHandleScreenPositions();
  for (const h of corners) {
    const d = Math.hypot(mx - h[0], my - h[1]);
    if (d >= HANDLE_HIT_RADIUS && d < ROTATE_ZONE_OUTER) return true;
  }
  return false;
};

const getEdgeHandleScreenPositions = (): [number, number][] => {
  const corners = getHandleScreenPositions();
  if (corners.length !== 4) return [];
  const [h0, h1, h2, h3] = corners as [[number,number],[number,number],[number,number],[number,number]];
  return [
    [(h0[0] + h1[0]) / 2, (h0[1] + h1[1]) / 2],
    [(h1[0] + h2[0]) / 2, (h1[1] + h2[1]) / 2],
    [(h2[0] + h3[0]) / 2, (h2[1] + h3[1]) / 2],
    [(h3[0] + h0[0]) / 2, (h3[1] + h0[1]) / 2],
  ];
};

const drawDistortOverlay = () => {
  if (!overlayGraphics) return;
  overlayGraphics.clear();
  if (store.controlMode !== 'aligner') return;

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

  // Corner handles (circles)
  [h0, h1, h2, h3].forEach(([hx, hy], i) => {
    const isActive = activeHandleIndex === i;
    overlayGraphics!.circle(hx, hy, isActive ? 8 : 6);
    overlayGraphics!.fill({ color: isActive ? 0xFFFFFF : 0xFFD700, alpha: 0.9 });
  });

  // Edge handles (squares)
  const edgeHandles = getEdgeHandleScreenPositions();
  edgeHandles.forEach(([hx, hy], i) => {
    const isActive = activeHandleIndex === i + 4;
    const size = isActive ? 6 : 4;
    overlayGraphics!.rect(hx - size, hy - size, size * 2, size * 2);
    overlayGraphics!.fill({ color: isActive ? 0xFFFFFF : 0xFFD700, alpha: 0.9 });
  });
};

// Reset cache when data changes
watch(() => store.targetData, () => {
    cachedLocalCenter = null;
    cachedLocalBBox = null;
    wasProjective = false;
    destroyChildren(); // free GPU memory from previous dataset before loading new one
    if (app && store.targetData) {
        const { width, height } = app.screen;
        const fitM = calculateFitMatrix(width, height);
        store.updateTargetTransform(fitM);
        transformBeforeDistort = mat3.clone(fitM);
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
    contentContainer.alpha = store.targetOpacity;

    if (isProjective(m)) {
      // Projective: spots are drawn at world positions directly; container stays at identity
      contentContainer.position.set(0, 0);
      contentContainer.rotation = 0;
      contentContainer.skew.set(0, 0);
      contentContainer.scale.set(1, 1);
      return;
    }

    const a = m[0]; const b = m[1]; const c = m[3]; const d = m[4];
    const tx = m[6]; const ty = m[7];
    const rotationX = Math.atan2(b, a);
    const rotationY = Math.atan2(-c, d);
    contentContainer.position.set(tx, ty);
    contentContainer.rotation = rotationX;
    contentContainer.skew.x = rotationX - rotationY;
    contentContainer.skew.y = 0;
    contentContainer.scale.set(Math.sqrt(a * a + b * b), Math.sqrt(c * c + d * d));
};

const updateContent = async () => {
    if (!app || !contentContainer) return;
    if (!store.targetData || !store.targetMeta) { destroyChildren(); return; }

    if (store.targetMeta.modality_type === 'IMAGE') {
        destroyChildren();
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
        // Reuse the persistent spotGraphics — only create it once per data load.
        // Subsequent calls (filter change, boost change, transform change) just
        // clear and redraw into the same object, keeping GPU memory constant.
        if (!spotGraphics) {
            destroyChildren();
            store.setTargetSpotBoost(1.0);
            spotGraphics = new Graphics();
            contentContainer.addChild(spotGraphics);
        }
        drawSpots(isProjective(store.targetTransform));
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
  // Use projective-aware center computation so flip/rotate/scale pivot is correct
  // even after a perspective distortion has been applied.
  const [cx_target, cy_target] = projectiveTransformPoint(mCurrent, localCenter[0] || 0, localCenter[1] || 0);

  const m = mat3.create();

  if (cmd.type === 'reset') {
     store.updateTargetTransform(createIdentity());
  } else if (cmd.type === 'resetDistort') {
     if (transformBeforeDistort) store.updateTargetTransform(mat3.clone(transformBeforeDistort));
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
    const isSpot = store.targetMeta?.modality_type !== 'IMAGE';
    const projective = isSpot && isProjective(store.targetTransform);
    // Redraw spots into the persistent Graphics when projective or on the one
    // transition frame back to affine (so spots return to local-space positions).
    if ((projective || wasProjective) && spotGraphics) drawSpots(projective);
    wasProjective = projective;
    updateContentTransform();
    render();
}, { deep: true });

watch(() => [store.globalZoom, store.viewOffset], () => {
    updateViewTransform();
    render();
}, { deep: true });

watch(() => [store.targetClassFilter, store.commonSpotBoost, store.targetSpotSize, store.targetForegroundMode], async () => {
    if (spotGraphics) {
        // Fast path: reuse existing Graphics, just redraw with new filter/boost/size
        drawSpots(isProjective(store.targetTransform));
        render();
    } else {
        await updateContent();
        render();
    }
}, { deep: true });

watch(
  () => [store.targetTransform, store.globalZoom, store.viewOffset, store.controlMode],
  () => { drawDistortOverlay(); app?.render(); },
  { deep: true }
);

const startDistortDrag = (handleIdx: number, mx: number, my: number, e: MouseEvent) => {
  // Snapshot the current affine transform before the first projective corner/edge drag.
  // This lets "Reset Distortion" undo only the projective warp, preserving panel adjustments.
  if (handleIdx < 8 && !isProjective(store.targetTransform)) {
    transformBeforeDistort = mat3.clone(store.targetTransform);
  }
  activeHandleIndex = handleIdx;
  isDragging = true;
  lastX = e.clientX;
  lastY = e.clientY;
  if (app) app.canvas.style.cursor = 'grabbing';
  const bbox = getTargetBBox();
  if (bbox) {
    const m = store.targetTransform;
    const bboxCorners: [number,number][] = [
      [bbox.minX, bbox.minY], [bbox.maxX, bbox.minY],
      [bbox.maxX, bbox.maxY], [bbox.minX, bbox.maxY],
    ];
    dragStartWorldCorners = bboxCorners.map(([lx, ly]) => projectiveTransformPoint(m, lx, ly));
  }
  dragStartMouseWorld = screenToWorld(mx, my);
};

const onMouseDown = (e: MouseEvent) => {
  if (store.controlMode === 'aligner' && app) {
    const rect = app.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    // Check corner handles first
    const cornerHandles = getHandleScreenPositions();
    for (let i = 0; i < cornerHandles.length; i++) {
      const h = cornerHandles[i]!;
      if (Math.hypot(mx - h[0], my - h[1]) < HANDLE_HIT_RADIUS) {
        startDistortDrag(i, mx, my, e);
        return;
      }
    }

    // Check edge handles (indices 4-7)
    const edgeHandles = getEdgeHandleScreenPositions();
    for (let i = 0; i < edgeHandles.length; i++) {
      const h = edgeHandles[i]!;
      if (Math.hypot(mx - h[0], my - h[1]) < HANDLE_HIT_RADIUS) {
        startDistortDrag(i + 4, mx, my, e);
        return;
      }
    }

    // Rotation zone: near corner, outside polygon → rotate (index 9)
    if (isInRotateZone(mx, my)) {
      activeHandleIndex = 9;
      isDragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      if (app) app.canvas.style.cursor = 'grabbing';
      return;
    }

    // Inside frame → translate all (index 8)
    if (isInsideDistortFrame(mx, my)) {
      startDistortDrag(8, mx, my, e);
      return;
    }
    return;
  }
  isDragging = true;
  lastX = e.clientX;
  lastY = e.clientY;
};

const onMouseMove = (e: MouseEvent) => {
  if (!isDragging && app) {
    const rect = app.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    if (mx >= 0 && my >= 0 && mx <= rect.width && my <= rect.height) {
      updateHoverCursor(mx, my);
    } else if (store.controlMode === 'aligner') {
      app.canvas.style.cursor = 'default';
    }
  }
  if (!isDragging) return;

  if (store.controlMode === 'aligner' && activeHandleIndex >= 0) {
    if (activeHandleIndex === 9) {
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
      const angleOld = Math.atan2(lastY - rect.top - cy_target_screen, lastX - rect.left - cx_target_screen);
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

    if (!app || dragStartWorldCorners.length !== 4) return;
    const rect = app.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const currentMouseWorld = screenToWorld(mx, my);

    // Total displacement in world space from drag start
    const dx = currentMouseWorld[0] - dragStartMouseWorld[0];
    const dy = currentMouseWorld[1] - dragStartMouseWorld[1];

    // Build new world corner positions
    let newWorldCorners: [[number,number],[number,number],[number,number],[number,number]];
    if (activeHandleIndex < 4) {
      // Corner drag: only the grabbed corner moves
      newWorldCorners = dragStartWorldCorners.map((c, i) =>
        i === activeHandleIndex ? [c[0] + dx, c[1] + dy] as [number, number] : c as [number, number]
      ) as [[number,number],[number,number],[number,number],[number,number]];
    } else if (activeHandleIndex < 8) {
      // Edge drag: both corners connected to this edge move together
      const cornerPair = EDGE_TO_CORNERS[activeHandleIndex - 4]!;
      newWorldCorners = dragStartWorldCorners.map((c, i) =>
        cornerPair.includes(i) ? [c[0] + dx, c[1] + dy] as [number, number] : c as [number, number]
      ) as [[number,number],[number,number],[number,number],[number,number]];
    } else {
      // Inside-frame drag: translate all 4 corners uniformly
      newWorldCorners = dragStartWorldCorners.map(c =>
        [c[0] + dx, c[1] + dy] as [number, number]
      ) as [[number,number],[number,number],[number,number],[number,number]];
    }

    const bbox = getTargetBBox();
    if (!bbox) return;
    const localCorners: [[number,number],[number,number],[number,number],[number,number]] = [
      [bbox.minX, bbox.minY], [bbox.maxX, bbox.minY],
      [bbox.maxX, bbox.maxY], [bbox.minX, bbox.maxY],
    ];

    const H = computeHomography(localCorners, newWorldCorners);
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
  if (app) app.canvas.style.cursor = '';
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
  if (distortRafId !== null) { cancelAnimationFrame(distortRafId); distortRafId = null; }
  resizeObserver?.disconnect();
  window.removeEventListener('mouseup', onMouseUp);
  window.removeEventListener('mousemove', onMouseMove);
  spotGraphics = null;
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
