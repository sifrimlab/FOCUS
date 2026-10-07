/** UI-only state of the alignment workspace. Never sent to the backend; survives sample changes. */
import { defineStore } from 'pinia';
import type { LayerRole } from '../composables/useLayer';

export const useUiStore = defineStore('ui', {
  state: () => ({
    /** Which layer panels show their details (classes, foreground, spot size). */
    expanded: { target: false, reference: false } as Record<LayerRole, boolean>,
    /** Mirrors the moving layer's undo history (canvas/useTransformHistory). */
    canUndo: false,
    canRedo: false,
  }),
});
