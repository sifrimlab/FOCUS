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

// Computes a matrix that centers the spot cloud at screen center with Y-flip,
// mirroring the same approach used by TargetCanvas.calculateFitMatrix.
const calculateFitMatrix = (width: number, height: number) => {
    const m = createIdentity();
    if (!store.referenceData || !store.referenceMeta) return m;
    if (store.referenceMeta.modality_type !== 'SPOT') return m;

    const spots = store.referenceData as SpotModalityPayload;
    if (!spots || spots.length === 0) return m;

    const spotSize = store.referenceSpotSize;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const s of spots) {
        minX = Math.min(minX, s.spatial[0]);
        maxX = Math.max(maxX, s.spatial[0]);
        minY = Math.min(minY, s.spatial[1]);
        maxY = Math.max(maxY, s.spatial[1]);
    }
    const rx = spotSize[0], ry = spotSize[1];
    minX -= rx / 2; maxX += rx / 2;
    minY -= ry / 2; maxY += ry / 2;

    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;

    translate(m, m, [width / 2, height / 2]);
    scale(m, m, [1, -1]);
    translate(m, m, [-cx, -cy]);

    return m;
};

// Applies store.referenceTransform to contentContainer, matching the pattern
// used by TargetCanvas.updateContentTransform.
const updateContentTransform = () => {
    if (!contentContainer || !store.referenceTransform) return;
    const m = store.referenceTransform;

    const a = m[0], b = m[1];
    const c = m[3], d = m[4];
    const tx = m[6], ty = m[7];

    contentContainer.position.set(tx, ty);
    contentContainer.rotation = Math.atan2(b, a);
    contentContainer.scale.set(Math.sqrt(a * a + b * b), Math.sqrt(c * c + d * d));

    const det = a * d - b * c;
    if (det < 0) contentContainer.scale.y *= -1;
};

const initPixi = async () => {
    if (!canvasContainer.value) return;

    if (app) {
        try { app.destroy(true, { children: true, texture: true }); }
        catch (e) { console.error('Error destroying app', e); }
        app = null;
    }

    const newApp = new Application();
    try {
        await newApp.init({
            resizeTo: canvasContainer.value,
            backgroundAlpha: 0.1,
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

    render();

    // After layout settles, recompute fit matrix and redraw with correct dimensions.
    setTimeout(() => {
        if (app && app.renderer) {
            app.resize();
            updateViewTransform();
            if (store.referenceData && store.referenceMeta && store.referenceMeta.modality_type === 'SPOT') {
                const { width, height } = app.screen;
                store.updateReferenceTransform(calculateFitMatrix(width, height));
            }
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

    if (app.screen.width === 0 || app.screen.height === 0) {
        app.resize();
    }

    if (!store.referenceData || !store.referenceMeta) {
        contentContainer.removeChildren();
        return;
    }

    updateViewTransform();
};

const updateContent = async () => {
    if (!app || !contentContainer) return;

    contentContainer.removeChildren();
    // Reset contentContainer transform; IMAGE positions its sprite directly,
    // SPOT drives it through updateContentTransform / store.referenceTransform.
    contentContainer.position.set(0, 0);
    contentContainer.scale.set(1, 1);
    contentContainer.rotation = 0;

    if (!store.referenceData || !store.referenceMeta) return;

    const { width, height } = app.screen;

    if (store.referenceMeta.modality_type === 'IMAGE') {
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

            const mRef = createIdentity();
            translate(mRef, mRef, [dx, dy]);
            scale(mRef, mRef, [s, s]);

            sprite.x = dx;
            sprite.y = dy;
            sprite.scale.set(s);

            contentContainer.addChild(sprite);
            store.updateReferenceTransform(mRef);
            if (app && app.renderer) app.render();
        } catch (e) {
            console.error('Failed to load texture', e);
        }
    } else if (store.referenceMeta.modality_type === 'SPOT') {
        const spots = store.referenceData as SpotModalityPayload;
        if (!spots || spots.length === 0) return;

        const spotSize = store.referenceSpotSize;
        const rx = spotSize[0];
        const ry = spotSize[1];

        const graphics = new Graphics();
        contentContainer.addChild(graphics);

        store.setReferenceSpotBoost(1.0);
        const finalBoost = store.commonSpotBoost;
        const drawRx = rx * finalBoost;
        const drawRy = ry * finalBoost;

        spots.forEach(spot => {
            const isClassVisible = store.referenceClassFilter.includes(spot.class);
            let isForegroundVisible = true;
            if (store.referenceForegroundMode === 'foreground') isForegroundVisible = spot.foreground;
            if (store.referenceForegroundMode === 'background') isForegroundVisible = !spot.foreground;
            if (!isClassVisible || !isForegroundVisible) return;

            const color = getReferenceColor(spot.class);
            graphics.rect(spot.spatial[0] - drawRx / 2, spot.spatial[1] - drawRy / 2, drawRx, drawRy);
            graphics.fill(color);
        });

        updateContentTransform();
        if (app && app.renderer) app.render();
    }
};

watch(() => [store.globalZoom, store.viewOffset], () => {
    updateViewTransform();
    if (app && app.renderer) app.render();
});

// When new data arrives, compute and persist the fit matrix before drawing,
// so updateContent can apply it even if called again later (e.g. from setTimeout).
watch(() => [store.referenceData, store.referenceMeta], () => {
    if (app && store.referenceData && store.referenceMeta) {
        if (store.referenceMeta.modality_type === 'SPOT') {
            const { width, height } = app.screen;
            if (width > 0 && height > 0) {
                store.updateReferenceTransform(calculateFitMatrix(width, height));
            }
        }
    }
    updateContent();
    if (app && app.renderer) app.render();
});

watch(() => [store.referenceClassFilter, store.commonSpotBoost, store.referenceSpotSize, store.referenceForegroundMode], async () => {
    await updateContent();
    if (app && app.renderer) app.render();
}, { deep: true });

onMounted(() => {
    initPixi();
    if (canvasContainer.value) {
        resizeObserver = new ResizeObserver(() => {
            if (app && app.renderer) {
                updateViewTransform();
                // Recompute fit matrix so centroid stays at screen centre after resize.
                if (store.referenceData && store.referenceMeta && store.referenceMeta.modality_type === 'SPOT') {
                    const { width, height } = app.screen;
                    store.updateReferenceTransform(calculateFitMatrix(width, height));
                }
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
        try { app.destroy(true, { children: true, texture: true }); }
        catch (e) { console.error('Error destroying app in unmount', e); }
        app = null;
    }
});
</script>

<template>
  <div ref="canvasContainer" class="absolute inset-0 w-full h-full pointer-events-none"></div>
</template>