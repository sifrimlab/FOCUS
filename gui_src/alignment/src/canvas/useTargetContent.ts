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

export function useTargetContent(layer: ReturnType<typeof usePixiLayer>, store: ReturnType<typeof useMainStore>) {
  let spotGraphics: Graphics | null = null; // reused across frames to avoid GPU alloc/free per draw

  // Properly destroy Pixi display objects so WebGL buffers are freed immediately.
  // removeChildren() alone only detaches them; GPU buffers would pile up until GC.
  const destroyChildren = () => {
    const content = layer.content();
    if (!content) return;
    const children = [...content.children];
    content.removeChildren();
    children.forEach(c => c.destroy({ children: true }));
    spotGraphics = null;
  };

  const drawSpots = (projective: boolean) => {
    if (!spotGraphics || !store.targetData || !store.targetMeta || store.targetMeta.modality_type === 'IMAGE') return;
    drawTargetSpots(spotGraphics, store.targetData as SpotModalityPayload, {
      spotSize: store.targetSpotSize,
      boost: store.commonSpotBoost,
      classFilter: store.targetClassFilter,
      foregroundMode: store.targetForegroundMode,
    }, store.targetTransform, projective);
  };

  const updateContentTransform = () => {
    const content = layer.content();
    if (!content || !store.targetTransform) return;
    applyTargetContentTransform(content, store.targetTransform, store.targetOpacity);
  };

  const loadImage = async (content: Container) => {
    destroyChildren();
    const url = URL.createObjectURL(store.targetData as Blob);
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
      if (!spotGraphics) {
        destroyChildren();
        store.setTargetSpotBoost(1.0);
        spotGraphics = new Graphics();
        content.addChild(spotGraphics);
      }
      drawSpots(isProjective(store.targetTransform));
    }
    updateContentTransform();
  };

  return {
    destroyChildren,
    drawSpots,
    updateContent,
    updateContentTransform,
    hasSpots: () => spotGraphics !== null,
    release: () => { spotGraphics = null; },
  };
}
