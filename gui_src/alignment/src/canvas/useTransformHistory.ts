/**
 * Records the moving layer's transform into the undo history, one entry per
 * gesture: a change is committed once the pointer is up, no number field is
 * being edited and the transform has been quiet for SETTLE_MS. A drag, a
 * held step button or a typed value is therefore one entry, not hundreds.
 * Undo and redo restore stored bytes; a restore equals the history's current
 * state, so it is never recorded as a change.
 */
import { onBeforeUnmount, onMounted, watch } from 'vue';
import type { mat3 } from 'gl-matrix';
import type { useMainStore } from '../store/main';
import { useUiStore } from '../store/ui';
import { createTransformHistory, type TransformSnapshot } from './transformHistory';

export const SETTLE_MS = 300;

type Store = ReturnType<typeof useMainStore>;

const sameBytes = (a: mat3, b: mat3) => {
  for (let i = 0; i < 9; i++) if (!Object.is(a[i], b[i])) return false;
  return true;
};

/**
 * Text-like fields hold a gesture open while focused; sliders and buttons do
 * not. Read live from document.activeElement: a field removed while focused
 * (its island closing) fires no focusout, but focus then leaves it.
 */
const isEditField = (el: EventTarget | null) =>
  el instanceof HTMLInputElement && (el.type === 'number' || el.type === 'text');

export function useTransformHistory(store: Store, distort: { before: mat3 | null }) {
  const ui = useUiStore();
  const history = createTransformHistory();
  let pending = false;
  let pointerDown = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const snapshot = (): TransformSnapshot => ({ transform: store.targetTransform, distortBefore: distort.before });

  const sync = () => {
    ui.canUndo = history.canUndo || pending;
    ui.canRedo = history.canRedo && !pending;
  };

  const cancelTimer = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };

  /** Commits the pending change, if the transform really differs from the current entry. */
  const flush = () => {
    cancelTimer();
    if (!pending) return;
    pending = false;
    const current = history.current();
    if (current && !sameBytes(current.transform, store.targetTransform)) history.push(snapshot());
    sync();
  };

  const schedule = () => {
    cancelTimer();
    if (pending && !pointerDown && !isEditField(document.activeElement)) timer = setTimeout(flush, SETTLE_MS);
  };

  watch(() => store.targetTransform, () => {
    const current = history.current();
    if (!current || sameBytes(current.transform, store.targetTransform)) return;
    pending = true;
    sync();
    schedule();
  }, { deep: true });

  const restore = (s: TransformSnapshot | null) => {
    if (!s) return;
    distort.before = s.distortBefore;
    store.updateTargetTransform(s.transform);
    sync();
  };

  const onPointerDown = () => { pointerDown = true; cancelTimer(); };
  const onPointerUp = () => { pointerDown = false; schedule(); };
  const onFocusIn = (e: FocusEvent) => { if (isEditField(e.target)) cancelTimer(); };
  // focusout fires before focus moves on; schedule once it has.
  const onFocusOut = (e: FocusEvent) => { if (isEditField(e.target)) setTimeout(schedule, 0); };

  onMounted(() => {
    window.addEventListener('pointerdown', onPointerDown, true);
    window.addEventListener('pointerup', onPointerUp, true);
    window.addEventListener('focusin', onFocusIn);
    window.addEventListener('focusout', onFocusOut);
  });
  onBeforeUnmount(() => {
    cancelTimer();
    window.removeEventListener('pointerdown', onPointerDown, true);
    window.removeEventListener('pointerup', onPointerUp, true);
    window.removeEventListener('focusin', onFocusIn);
    window.removeEventListener('focusout', onFocusOut);
    ui.canUndo = false;
    ui.canRedo = false;
  });

  return {
    /** Starts a new history at the given state (a new sample's fit). */
    reset(transform: mat3, distortBefore: mat3 | null) {
      cancelTimer();
      pending = false;
      history.reset({ transform, distortBefore });
      sync();
    },
    undo() { flush(); restore(history.undo()); },
    redo() { flush(); restore(history.redo()); },
    /** Applies a rewrite (resize shift) to every stored state. */
    map(fn: (m: mat3) => mat3) { history.map(fn); },
  };
}
