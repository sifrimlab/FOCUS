/** Maps app and pipeline state to the ambient backdrop hue (DESIGN.md 5.1). */
import { computed } from 'vue';
import { useMainStore } from '../store/main';

export type AmbientState =
  | 'idle' | 'run' | 'wait' | 'done' | 'err'
  | 'pre' | 'align' | 'annot' | 'reg' | 'comp';

/** Each running stage has its own hue, cool to warm along the pipeline. */
const STAGE_AMBIENT: Record<string, AmbientState> = {
  preprocessing: 'pre',
  alignment: 'align',
  annotation_transfer: 'annot',
  registration: 'reg',
  compiling: 'comp',
};

export function useAmbientState() {
  const store = useMainStore();

  return computed<AmbientState>(() => {
    switch (store.currentView) {
      case 'complete':
        return 'done';
      case 'running': {
        const { state, stage } = store.pipelineStatus;
        if (state === 'error') return 'err';
        if (state === 'alignment_waiting') return 'wait';
        return (stage && STAGE_AMBIENT[stage]) || 'run';
      }
      default:
        return 'idle';
    }
  });
}
