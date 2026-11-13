/**
 * Role-keyed view of one layer in the store, so a single set of components
 * serves both layers. "target" is the moving layer, "reference" the fixed one.
 */
import { computed, reactive } from 'vue';
import { useMainStore, type ForegroundMode } from '../store/main';
import { classColor } from '../utils/colors';

export type LayerRole = 'target' | 'reference';

export function useLayer(role: LayerRole) {
  const store = useMainStore();
  const isTarget = role === 'target';
  const meta = computed(() => (isTarget ? store.targetMeta : store.referenceMeta));

  return reactive({
    role,
    meta,
    name: computed(() => meta.value?.modality_name || (isTarget ? 'Target' : 'Reference')),
    isSpot: computed(() => meta.value?.modality_type === 'SPOT'),
    classes: computed(() => (isTarget ? store.targetSpotClasses : store.referenceSpotClasses)),
    classFilter: computed({
      get: () => (isTarget ? store.targetClassFilter : store.referenceClassFilter),
      set: (v: number[]) => { if (isTarget) store.targetClassFilter = v; else store.referenceClassFilter = v; },
    }),
    foregroundMode: computed({
      get: () => (isTarget ? store.targetForegroundMode : store.referenceForegroundMode),
      set: (v: ForegroundMode) => { if (isTarget) store.targetForegroundMode = v; else store.referenceForegroundMode = v; },
    }),
    /** Channels of a multichannel image layer (empty for RGB images and spots). */
    channels: computed(() => meta.value?.channels ?? []),
    shownChannels: computed(() => (isTarget ? store.targetChannels : store.referenceChannels)),
    setChannels: (channels: number[]) => store.setChannels(role, channels),
    /** The store's own array: fields assign into it in place. */
    spotSize: computed(() => (isTarget ? store.targetSpotSize : store.referenceSpotSize)),
    /** Clusters at a resolution or a feature image; null for plain classes and images. */
    view: computed(() => store.spotViews[role]),
    display: computed(() => (isTarget ? store.targetDisplay : store.referenceDisplay)),
    colorOf: (cls: number) => {
      const display = isTarget ? store.targetDisplay : store.referenceDisplay;
      return display ? display.colors[cls] ?? classColor(meta.value, cls, isTarget) : classColor(meta.value, cls, isTarget);
    },
    labelOf: (cls: number) => {
      const display = isTarget ? store.targetDisplay : store.referenceDisplay;
      return display ? display.labels[cls] ?? String(cls) : meta.value?.cluster_labels?.[cls] ?? String(cls);
    },
  });
}

export type Layer = ReturnType<typeof useLayer>;
