/**
 * Press-and-hold repeat for step buttons (DESIGN.md 8.18): the action fires
 * once on press, then every `intervalMs` until release or pointer leave.
 */
import { onBeforeUnmount } from 'vue';

export function useHoldRepeat(intervalMs = 100) {
  let timer: ReturnType<typeof setInterval> | null = null;

  const stop = () => {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  };

  const start = (action: () => void) => {
    stop();
    action();
    timer = setInterval(action, intervalMs);
  };

  onBeforeUnmount(stop);

  return { start, stop };
}
