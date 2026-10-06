<script setup lang="ts">
/**
 * Samples of the current modality (or stage): "Sample 2 of 5 · name", a bar,
 * and one chip per included sample (done / current / pending). Above
 * MAX_CHIPS the chips are omitted and the bar and counts carry the information.
 */
import { computed } from 'vue';
import { injectRunProgress } from '../../composables/useRunProgress';
import ProgressBar from '../ui/ProgressBar.vue';
import AppIcon from '../ui/AppIcon.vue';

const MAX_CHIPS = 40;

const run = injectRunProgress();
const s = run.status;

const samples = run.includedSamples;
const doneCount = computed(() => Object.values(run.sampleStates.value).filter(v => v === 'done').length);
const total = computed(() => s.value.total_samples || samples.value.length);
const percent = computed(() => (total.value ? Math.min(100, (doneCount.value / total.value) * 100) : 0));
</script>

<template>
  <section class="flex flex-col gap-3" aria-label="Samples">
    <div class="flex items-baseline justify-between gap-3" aria-live="polite">
      <div class="flex min-w-0 items-baseline gap-2">
        <span class="type-footnote nums text-fg3 shrink-0">
          <template v-if="s.current_sample_index">Sample {{ s.current_sample_index }} of {{ total }}</template>
          <template v-else>Samples</template>
        </span>
        <span v-if="s.current_sample" class="type-mono text-fg1 ellipsis" :title="s.current_sample">{{ s.current_sample }}</span>
      </div>
      <span class="type-footnote nums text-fg3 shrink-0">{{ doneCount }} done</span>
    </div>

    <ProgressBar tone="stage" label="Samples completed" :value="percent" :active="false" />

    <ul v-if="samples.length <= MAX_CHIPS" class="flex flex-wrap gap-1.5">
      <li
        v-for="id in samples"
        :key="id"
        class="chip type-mono-small"
        :class="`chip--${run.sampleStates.value[id]}`"
        :title="`${id}: ${run.sampleStates.value[id]}`"
      >
        <AppIcon v-if="run.sampleStates.value[id] === 'done'" name="check" :size="12" />
        <span v-else-if="run.sampleStates.value[id] === 'current'" class="chip__dot" aria-hidden="true" />
        <span class="ellipsis">{{ id }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 180px;
  height: var(--control-sm);
  padding: 0 10px;
  border-radius: var(--radius-full);
  color: var(--fg3);
  box-shadow: inset 0 0 0 1px var(--separator);
  transition: background-color var(--dur-base) var(--ease-out), color var(--dur-base) var(--ease-out);
}
.chip--done { color: var(--fg2); background: var(--mat-inset-fill); box-shadow: none; }
.chip--current {
  color: var(--fg1);
  background: color-mix(in srgb, var(--stage-accent) 20%, transparent);
  box-shadow: inset 0 0 0 1.5px var(--stage-accent);
}
.chip__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--stage-accent);
  animation: chip-pulse 1.6s var(--ease-in-out) infinite;
}
@keyframes chip-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
@media (prefers-reduced-motion: reduce) {
  .chip__dot { animation: none; }
}
</style>
