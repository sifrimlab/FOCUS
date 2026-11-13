/**
 * Content of the moving layer inside its Pixi container: the image sprite,
 * or one persistent Graphics with all spots (cleared and redrawn, so GPU
 * memory stays constant while dragging), plus the container transform.
 */
import { Graphics, Sprite, Texture, type Container } from 'pixi.js';
import type { useMainStore } from '../store/main';
import type { SpotModalityPayload } from '../api/types';
import type { usePixiLayer } from './usePixiLayer';
import { drawTargetSpots } from './spotLayer';
import { applyTargetContentTransform } from './contentTransform';
import { isProjective } from './targetGeometry';
import { NativeSpotLayer } from './nativeSpotLayer';
import { classColor } from '../utils/colors';
import { matrixPointMap, warpPointMap, type PointMap } from './warpView';

export function useTargetContent(layer: ReturnType<typeof usePixiLayer>, store: ReturnType<typeof useMainStore>) {
  let spotGraphics: Graphics | null = null; // reused across frames to avoid GPU alloc/free per draw
  let nativeSpots: NativeSpotLayer | null = null;

  // Properly destroy Pixi display objects so WebGL buffers are freed immediately.
  // removeChildren() alone only detaches them; GPU buffers would pile up until GC.
  const destroyChildren = () => {
    const content = layer.content();
    if (!content) return;
    const children = [...content.children];
    content.removeChildren();
    children.forEach(c => c.destroy({ children: true }));
    spotGraphics = null;
    nativeSpots = null;
  };

  /** Per-spot map of the current state: the landmark warp, a projective matrix, or none (affine). */
  const pointMap = (): PointMap | null => {
    if (store.targetMeta?.modality_type === 'IMAGE') return null;
    if (store.hasLandmarkWarp) return warpPointMap(store);
    if (isProjective(store.targetTransform)) return matrixPointMap(store.targetTransform);
    return null;
  };

  /** True when spots are drawn at world positions (the container stays at identity). */
  const isPointwise = () => pointMap() !== null;

  const drawSpots = () => {
    if ((!spotGraphics && !nativeSpots) || !store.targetData || !store.targetMeta || store.targetMeta.modality_type === 'IMAGE') return;
    const style = {
      spotSize: store.targetSpotSize,
      boost: store.commonSpotBoost,
      classFilter: store.targetClassFilter,
      foregroundMode: store.targetForegroundMode,
      basis: store.targetMeta.spot_basis,
      colorOf: (cls: number) => classColor(store.targetMeta, cls, true),
    };
    const map = pointMap();
    if (nativeSpots) nativeSpots.update(style, map ?? undefined);
    else drawTargetSpots(spotGraphics!, store.targetData as SpotModalityPayload, style, map);
  };

  const updateContentTransform = () => {
    const content = layer.content();
    if (!content || !store.targetTransform) return;
    applyTargetContentTransform(content, store.targetTransform, store.targetOpacity, store.hasLandmarkWarp);
  };

  const loadImage = async (content: Container) => {
    destroyChildren();
    const url = URL.createObjectURL((store.targetImage ?? store.targetData) as Blob);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      URL.revokeObjectURL(url);
      content.addChild(new Sprite(Texture.from(img)));
      const app = layer.app();
      if (app && app.renderer) app.render();
    } catch (e) { console.error(e); }
  };

  const updateContent = async () => {
    const app = layer.app();
    const content = layer.content();
    if (!app || !content) return;
    if (!store.targetData || !store.targetMeta) { destroyChildren(); return; }

    if (store.targetMeta.modality_type === 'IMAGE') {
      await loadImage(content);
    } else {
      // Create the persistent Graphics once per data load; later calls only redraw.
      if (!spotGraphics && !nativeSpots) {
        destroyChildren();
        store.setTargetSpotBoost(1.0);
        if (store.targetMeta.spot_encoding) {
          nativeSpots = new NativeSpotLayer(store.targetData as SpotModalityPayload, store.targetMeta, true, store.targetDisplay);
          content.addChild(nativeSpots);
        } else {
          spotGraphics = new Graphics();
          content.addChild(spotGraphics);
        }
      }
      drawSpots();
    }
    updateContentTransform();
  };

  return {
    destroyChildren,
    drawSpots,
    isPointwise,
    updateContent,
    updateContentTransform,
    hasSpots: () => spotGraphics !== null || nativeSpots !== null,
    cull: () => { const app = layer.app(); if (app) nativeSpots?.cull(app.screen); },
    release: () => { spotGraphics = null; nativeSpots = null; },
  };
}
