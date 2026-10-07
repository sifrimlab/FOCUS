/** True while the window is narrower than the alignment workspace supports. */
import { onMounted, onUnmounted, ref } from 'vue';

export const MIN_VIEWPORT_WIDTH = 720;

export function useViewportGuard() {
  const isTooSmall = ref(window.innerWidth < MIN_VIEWPORT_WIDTH);
  const check = () => { isTooSmall.value = window.innerWidth < MIN_VIEWPORT_WIDTH; };
  onMounted(() => window.addEventListener('resize', check));
  onUnmounted(() => window.removeEventListener('resize', check));
  return isTooSmall;
}
