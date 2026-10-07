/**
 * Open state and fixed-position coordinates for a popover anchored to a
 * trigger element: below its right edge (`bottom-end`, default) or beside
 * its right side, bottom-aligned (`right-end`, for controls on a left rail).
 *
 * Popovers are teleported to <body>: glass cards create their own stacking
 * and backdrop contexts, so a popover nested inside one would be clipped by
 * later cards and could not blur what lies outside its card.
 */
import { onBeforeUnmount, ref, type Ref } from 'vue';

const GAP_PX = 8;

export type PopoverPlacement = 'bottom-end' | 'right-end';

export function useAnchoredPopover(anchor: Ref<HTMLElement | null>, placement: PopoverPlacement = 'bottom-end') {
  const isOpen = ref(false);
  const style = ref<Record<string, string>>({});

  const place = () => {
    const rect = anchor.value?.getBoundingClientRect();
    if (!rect) return;
    style.value = placement === 'bottom-end'
      ? { top: `${rect.bottom + GAP_PX}px`, right: `${window.innerWidth - rect.right}px` }
      : { left: `${rect.right + GAP_PX}px`, bottom: `${window.innerHeight - rect.bottom}px` };
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
