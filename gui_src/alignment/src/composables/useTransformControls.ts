/**
 * Transform controls of the moving layer and the camera. Values typed or
 * stepped here become store commands that the target canvas executes; the
 * arithmetic (steps, rounding for display) is kept from the original panel.
 */
import { computed } from 'vue';
import { useMainStore, type CommandType } from '../store/main';

export const SCALE_STEP = 0.01;
export const ROTATION_STEP = 1;
const ZOOM_OUT = 0.98;
const ZOOM_IN = 1.02;

export function useTransformControls() {
  const store = useMainStore();

  const sendCommand = (type: CommandType, value?: any) => {
    store.pendingCommand = { type, value };
  };

  const scale = computed({
    get: () => {
      const m = store.targetTransform;
      const val = Math.hypot(m[0], m[1]);
      return parseFloat(val.toFixed(4));
    },
    set: (val) => {
      sendCommand('setScale', val);
    },
  });

  const rotation = computed({
    get: () => {
      const m = store.targetTransform;
      let deg = Math.atan2(m[1], m[0]) * 180 / Math.PI;
      if (deg < 0) deg += 360;
      return parseFloat((deg % 360).toFixed(4));
    },
    set: (val) => {
      sendCommand('setRotation', val);
    },
  });

  const zoom = computed({
    get: () => store.globalZoom,
    set: (val) => { store.globalZoom = val; },
  });

  const mode = computed({
    get: () => store.controlMode,
    set: (m) => store.setControlMode(m),
  });

  const stepScale = (direction: -1 | 1) => {
    const delta = direction < 0 ? -SCALE_STEP : SCALE_STEP;
    const m = store.targetTransform;
    const exactScale = Math.hypot(m[0], m[1]);
    sendCommand('setScale', exactScale + delta);
  };

  const stepRotation = (direction: -1 | 1) => {
    const delta = direction < 0 ? -ROTATION_STEP : ROTATION_STEP;
    const m = store.targetTransform;
    const deg = Math.atan2(m[1], m[0]) * 180 / Math.PI;
    sendCommand('setRotation', deg + delta);
  };

  const stepZoom = (direction: -1 | 1) => {
    store.globalZoom *= direction < 0 ? ZOOM_OUT : ZOOM_IN;
  };

  return {
    mode, scale, rotation, zoom,
    stepScale, stepRotation, stepZoom,
    resetScale: () => sendCommand('resetScale'),
    resetRotation: () => sendCommand('resetRotation'),
    resetZoom: () => { store.globalZoom = 1.0; },
    flip: (axis: 'horizontal' | 'vertical') => sendCommand('flip', axis === 'horizontal'),
    resetDistortion: () => sendCommand('resetDistort'),
    resetTransform: () => sendCommand('reset'),
    undo: () => sendCommand('undo'),
    redo: () => sendCommand('redo'),
  };
}
