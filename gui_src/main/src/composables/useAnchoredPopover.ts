/**
 * Open state and fixed-position coordinates for a popover anchored below
 * the right edge of a trigger element.
 *
 * Popovers are teleported to <body>: glass cards create their own stacking
 * and backdrop contexts, so a popover nested inside one would be clipped by
 * later cards and could not blur what lies outside its card.
 */
import { onBeforeUnmount, ref, type Ref } from 'vue';

const GAP_PX = 8;

export function useAnchoredPopover(anchor: Ref<HTMLElement | null>) {
  const isOpen = ref(false);
  const style = ref<Record<string, string>>({});

  const place = () => {
    const rect = anchor.value?.getBoundingClientRect();
    if (!rect) return;
    style.value = {
      top: `${rect.bottom + GAP_PX}px`,
      right: `${window.innerWidth - rect.right}px`,
    };
  };

  const listen = (on: boolean) => {
    if (on) {
      window.addEventListener('scroll', place, true);
      window.addEventListener('resize', place);
    } else {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    }
  };

  const open = () => {
    place();
    isOpen.value = true;
    listen(true);
  };

  const close = () => {
    isOpen.value = false;
    listen(false);
  };

  onBeforeUnmount(close);

  return { isOpen, style, open, close };
}
