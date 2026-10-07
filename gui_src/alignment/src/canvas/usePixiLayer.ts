/**
 * Lifecycle of one Pixi layer bound to a container element: create the
 * application (transparent; the canvas ground is CSS), add a view (camera)
 * container holding a content (layer) container, settle once the layout is
 * final, follow container resizes, and free the GPU resources on unmount.
 */
import { onMounted, onUnmounted, type Ref } from 'vue';
import { Application, Container } from 'pixi.js';

export interface PixiLayerHooks {
  /** Called once the containers exist, before the first settle. */
  onInit: (app: Application, view: Container, content: Container) => void;
  /** Called 100 ms after init, once the layout has settled and the renderer was resized. */
  onSettle: (app: Application) => void | Promise<void>;
  /** Called on every container resize while the renderer is alive, after the renderer took the new size. */
  onResize: (app: Application) => void;
}

export function usePixiLayer(container: Ref<HTMLElement | null>, hooks: PixiLayerHooks) {
  let app: Application | null = null;
  let view: Container | null = null;
  let content: Container | null = null;
  let resizeObserver: ResizeObserver | null = null;

  const init = async () => {
    if (!container.value) return;

    if (app) {
      try { app.destroy(true, { children: true, texture: true }); }
      catch (e) { console.error(e); }
      app = null;
    }

    const newApp = new Application();
    try {
      await newApp.init({
        resizeTo: container.value,
        backgroundAlpha: 0,
        antialias: true,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
        preference: 'webgl',
        autoStart: false,
      });
    } catch (e) {
      console.error('Pixi init failed', e);
      return;
    }

    if (!container.value) {
      newApp.destroy(true);
      return;
    }

    app = newApp;
    container.value.appendChild(app.canvas);
    app.canvas.style.width = '100%';
    app.canvas.style.height = '100%';
    app.canvas.style.display = 'block';

    view = new Container();
    app.stage.addChild(view);
    content = new Container();
    view.addChild(content);

    hooks.onInit(app, view, content);

    // After layout settles, resize the renderer and let the layer redraw.
    setTimeout(() => {
      if (app && app.renderer) {
        app.resize();
        hooks.onSettle(app);
      }
    }, 100);
  };

  onMounted(() => {
    init();
    if (container.value) {
      resizeObserver = new ResizeObserver(() => {
        // Pixi's own resizeTo follows on a later frame; resize now so app.screen is current.
        if (app && app.renderer) {
          app.resize();
          hooks.onResize(app);
        }
      });
      resizeObserver.observe(container.value);
    }
  });

  onUnmounted(() => {
    resizeObserver?.disconnect();
    if (app) {
      try { app.destroy(true, { children: true, texture: true }); }
      catch (e) { console.error(e); }
      app = null;
    }
  });

  return {
    app: () => app,
    view: () => view,
    content: () => content,
  };
}
