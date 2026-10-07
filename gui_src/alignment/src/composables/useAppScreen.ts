/**
 * Which screen the app shows, in the original priority order: small
 * viewport, backend down, error, loading, finished, then the workspace.
 */
import { computed } from 'vue';
import type { AmbientState } from '@focus/ui/types/ambient';
import { useMainStore } from '../store/main';
import { STATUS_SCREENS, type StatusKind, type StatusScreenSpec } from '../screens/statusScreens';
import { useViewportGuard } from './useViewportGuard';

export type AppScreen =
  | { kind: 'workspace'; ambient: AmbientState }
  | ({ kind: StatusKind } & StatusScreenSpec);

export function useAppScreen() {
  const store = useMainStore();
  const isTooSmall = useViewportGuard();

  return computed<AppScreen>(() => {
    const kind: StatusKind | 'workspace' =
      isTooSmall.value ? 'small'
      : store.isBackendDown ? 'offline'
      : store.error ? 'error'
      : store.isLoading ? 'loading'
      : store.isFinished ? 'finished'
      : 'workspace';
    if (kind === 'workspace') return { kind, ambient: 'align' };
    return { kind, ...STATUS_SCREENS[kind](store) };
  });
}
