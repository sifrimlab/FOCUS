/**
 * A ticking clock on the backend's time base. The status carries
 * `server_now`; the offset to the browser clock is kept so elapsed times are
 * right even when the GUI is served from another machine (e.g. an SSH tunnel).
 */
import { computed, onBeforeUnmount, ref, type Ref } from 'vue';

const TICK_MS = 1000;

export function useServerClock(serverNow: Ref<number | null | undefined>) {
  const browserNow = ref(Date.now() / 1000);
  const timer = window.setInterval(() => { browserNow.value = Date.now() / 1000; }, TICK_MS);
  onBeforeUnmount(() => window.clearInterval(timer));

  // Offset measured at the latest status poll.
  const offset = computed(() => (serverNow.value ? serverNow.value - Date.now() / 1000 : 0));

  /** Current server time, updated every second. */
  const now = computed(() => browserNow.value + offset.value);

  /** Seconds between start and end (or now, while running). */
  const elapsed = (start: number | null | undefined, end?: number | null) =>
    start ? (end ?? now.value) - start : 0;

  return { now, elapsed };
}
