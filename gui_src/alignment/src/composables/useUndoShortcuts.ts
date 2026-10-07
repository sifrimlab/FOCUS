/**
 * Cmd/Ctrl+Z undoes and Shift+Cmd/Ctrl+Z (or Ctrl+Y) redoes a change of the
 * moving layer. Ignored while typing in a field, which keeps its own text undo.
 */
import { onBeforeUnmount, onMounted } from 'vue';
import { useTransformControls } from './useTransformControls';

const isTyping = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));

export function useUndoShortcuts() {
  const { undo, redo } = useTransformControls();

  const onKey = (e: KeyboardEvent) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey || isTyping(e.target)) return;
    const key = e.key.toLowerCase();
    if (key === 'z') {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
    } else if (key === 'y' && e.ctrlKey && !e.shiftKey) {
      e.preventDefault();
      redo();
    }
  };

  onMounted(() => window.addEventListener('keydown', onKey));
  onBeforeUnmount(() => window.removeEventListener('keydown', onKey));
}
