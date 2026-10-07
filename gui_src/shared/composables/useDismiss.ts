/**
 * Closes a floating element (menu, expanded island) on a pointer press
 * outside all of its roots or on Escape. Listens only while open; presses
 * inside any root, such as the trigger, are left to the element.
 */
import { onBeforeUnmount, watch, type Ref } from 'vue';

export function useDismiss(roots: Ref<HTMLElement | null>[], isOpen: Readonly<Ref<boolean>>, close: () => void) {
  const onPointerDown = (e: PointerEvent) => {
    const t = e.target;
    if (!(t instanceof Node) || !roots.some(r => r.value?.contains(t))) close();
  };
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };

  const listen = (on: boolean) => {
    const method = on ? 'addEventListener' : 'removeEventListener';
    window[method]('pointerdown', onPointerDown as EventListener, true);
    window[method]('keydown', onKey as EventListener);
  };

  watch(isOpen, listen, { immediate: true });
  onBeforeUnmount(() => listen(false));
}
