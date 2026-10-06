/** Maps app and pipeline state to the ambient backdrop hue (DESIGN.md 5.1). */
import { computed } from 'vue';
import { useMainStore } from '../store/main';

export type AmbientState = 'idle' | 'run' | 'wait' | 'done' | 'err';

export function useAmbientState() {
  const store = useMainStore();

  return computed<AmbientState>(() => {
    switch (store.currentView) {
      case 'complete':
        return 'done';
      case 'running': {
        const state = store.pipelineStatus.state;
        if (state === 'error') return 'err';
        if (state === 'alignment_waiting') return 'wait';
        return 'run';
      }
      default:
        return 'idle';
    }
  });
}
