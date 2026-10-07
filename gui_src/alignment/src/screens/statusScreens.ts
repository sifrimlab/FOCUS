/**
 * The full-screen states of the alignment GUI, as data: card content,
 * backdrop hue and an optional action. Rendered by the shared StatusScreen.
 */
import type { AmbientState } from '@focus/ui/types/ambient';
import type { IconName } from '@focus/ui/icons/paths';
import type { useMainStore } from '../store/main';
import { MIN_VIEWPORT_WIDTH } from '../composables/useViewportGuard';

export type StatusKind = 'small' | 'offline' | 'error' | 'loading' | 'finished';

export interface StatusCard {
  tone: 'primary' | 'success' | 'warning' | 'danger';
  icon: IconName;
  title: string;
  message?: string;
  meta?: string;
  detail?: string;
  progress?: number | null;
}

export interface StatusScreenSpec {
  ambient: AmbientState;
  card: StatusCard;
  action?: { label: string; icon: IconName; run: () => void };
}

type Store = ReturnType<typeof useMainStore>;

const sampleCount = (store: Store) =>
  store.sampleInfo ? `Sample ${store.sampleInfo.sample_index} of ${store.sampleInfo.total_samples_count}` : undefined;

function loading(store: Store): StatusScreenSpec {
  const info = store.sampleInfo;
  const progress = !info ? null : info.total_samples_count === 0 ? 0 : (info.sample_index / info.total_samples_count) * 100;
  const title = store.loadingMessage ? 'Saving alignment' : info ? 'Loading sample' : 'Reading dataset';
  return {
    ambient: 'align',
    card: { tone: 'primary', icon: 'arrow-path', title, message: store.loadingMessage ?? undefined, meta: sampleCount(store), progress },
  };
}

export const STATUS_SCREENS: Record<StatusKind, (store: Store) => StatusScreenSpec> = {
  small: () => ({
    ambient: 'idle',
    card: {
      tone: 'warning',
      icon: 'computer-desktop',
      title: 'Screen too small',
      message: `Use a desktop browser at least ${MIN_VIEWPORT_WIDTH} px wide.`,
    },
  }),
  offline: () => ({
    ambient: 'err',
    card: {
      tone: 'danger',
      icon: 'signal-slash',
      title: 'Backend not running',
      message: 'The alignment server does not respond. Make sure it is running, then retry.',
    },
    action: { label: 'Retry connection', icon: 'arrow-path', run: () => window.location.reload() },
  }),
  error: store => ({
    ambient: 'err',
    card: {
      tone: 'danger',
      icon: 'exclamation-triangle',
      title: 'Alignment error',
      message: 'The alignment could not continue. Check the server logs, then close this window and restart the pipeline.',
      detail: store.error ?? undefined,
    },
  }),
  loading,
  finished: () => ({
    ambient: 'done',
    card: {
      tone: 'success',
      icon: 'check-circle',
      title: 'Alignment completed',
      message: 'All samples are aligned. You can close this window.',
    },
  }),
};
