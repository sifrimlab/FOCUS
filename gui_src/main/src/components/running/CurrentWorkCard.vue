<script setup lang="ts">
/**
 * What the pipeline is doing now, top-down: stage and modality, the current
 * step, then the samples. Sections appear only when the stage reports them.
 */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import { injectRunProgress } from '../../composables/useRunProgress';
import { formatDuration } from '../../utils/duration';
import GlassCard from '@focus/ui/components/ui/GlassCard.vue';
import TagLabel from '@focus/ui/components/ui/TagLabel.vue';
import StepTrack from './StepTrack.vue';
import SampleTrack from './SampleTrack.vue';

const store = useMainStore();
const run = injectRunProgress();
const s = run.status;

const stage = run.activeStage;
const modality = computed(() => run.activeModality.value);
const modalityType = computed(() => {
  const m = store.config.modalities.find(x => x.name === modality.value?.name);
  return m ? store.displayName(m.type) : '';
});

const eyebrow = computed(() => {
  if (!stage.value) return 'Starting';
  if (!modality.value) {
    // No modality level: the title already names the stage, so give its position instead.
    const index = run.stages.value.findIndex(x => x.id === stage.value!.id);
    return `Stage ${index + 1} of ${run.stages.value.length}`;
  }
  const pos = s.value.total_modalities > 0 ? ` · modality ${s.value.current_modality_index} of ${s.value.total_modalities}` : '';
  return `${stage.value.label}${pos}`;
});

const title = computed(() => modality.value?.name ?? stage.value?.label ?? 'Preparing the run');
const seconds = computed(() =>
  modality.value ? run.elapsed(modality.value.started_at, modality.value.ended_at) : stage.value?.seconds ?? null,
);

const showSteps = computed(() => !!s.value.sub_step);
const showSamples = run.samplesVisible;
</script>

<template>
  <GlassCard class="flex flex-col">
    <header class="flex items-start justify-between gap-4 pb-5">
      <div class="flex min-w-0 flex-col gap-1">
        <span class="type-footnote text-fg2 flex items-center gap-2">
          <span class="accent-dot" aria-hidden="true" />{{ eyebrow }}
        </span>
        <div class="flex min-w-0 items-center gap-2.5">
          <h2 class="type-title-2 text-fg1 ellipsis" :title="title">{{ title }}</h2>
          <TagLabel v-if="modalityType">{{ modalityType }}</TagLabel>
        </div>
      </div>
      <span v-if="seconds !== null" class="type-footnote nums text-fg3 shrink-0 pt-1">{{ formatDuration(seconds) }}</span>
    </header>

    <div v-if="showSteps" class="border-t border-separator py-5">
      <!-- The current-sample chip already shows activity; the shimmer is only needed without it. -->
      <StepTrack :idle-shimmer="!showSamples" />
    </div>
    <div v-if="showSamples" class="border-t border-separator pt-5 pb-1">
      <SampleTrack />
    </div>
    <p v-if="!showSteps && !showSamples" class="type-callout text-fg3 border-t border-separator pt-5">
      {{ s.message || 'Starting…' }}
    </p>
  </GlassCard>
</template>

<style scoped>
.accent-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--stage-accent);
}
</style>
