<script setup lang="ts">
/**
 * Current step of the active modality: "Step 3 of 8", its label, one segment
 * per step, and item progress ("1,840 of 4,096 tiles") when the step counts items.
 */
import { computed } from 'vue';
import { injectRunProgress } from '../../composables/useRunProgress';
import ProgressBar from '../ui/ProgressBar.vue';

const run = injectRunProgress();
const s = run.status;

const segments = computed(() => Array.from({ length: s.value.sub_step_total }, (_, i) => i + 1));
const hasItems = computed(() => s.value.sub_step_items_total > 0);
const percent = computed(() => Math.min(100, (s.value.sub_step_progress / s.value.sub_step_items_total) * 100));

const count = (n: number) => n.toLocaleString();
const plural = (unit: string) => (/(s|sh|ch|x)$/.test(unit) ? `${unit}es` : `${unit}s`);
const unitLabel = computed(() => {
  const unit = s.value.sub_step_unit;
  if (!unit) return '';
  return s.value.sub_step_items_total === 1 ? unit : plural(unit);
});

const segmentState = (n: number) =>
  n < s.value.sub_step_index ? 'done' : n === s.value.sub_step_index ? 'current' : 'pending';
</script>

<template>
  <section class="flex flex-col gap-3" aria-label="Current step">
    <div class="flex flex-col gap-0.5" aria-live="polite">
      <span v-if="s.sub_step_total > 0" class="type-footnote nums text-fg3">
        Step {{ s.sub_step_index }} of {{ s.sub_step_total }}
      </span>
      <span v-else class="type-footnote text-fg3">Current step</span>
      <p class="type-headline text-fg1 ellipsis" :title="s.sub_step ?? ''">{{ run.stepLabel.value }}</p>
    </div>

    <div v-if="segments.length > 1" class="flex gap-1" aria-hidden="true">
      <span v-for="n in segments" :key="n" class="seg" :class="`seg--${segmentState(n)}`" />
    </div>

    <div class="flex flex-col gap-1.5">
      <ProgressBar
        tone="stage"
        :label="`Progress of ${run.stepLabel.value}`"
        :value="hasItems ? percent : run.finished.value ? 100 : null"
      />
      <span v-if="hasItems" class="type-footnote nums text-fg3 self-end">
        {{ count(s.sub_step_progress) }} of {{ count(s.sub_step_items_total) }} {{ unitLabel }}
      </span>
    </div>
  </section>
</template>

<style scoped>
.seg {
  flex: 1;
  height: 4px;
  border-radius: var(--radius-full);
  background: var(--mat-inset-fill);
  transition: background-color var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out);
}
.seg--done { background: var(--stage-accent); opacity: 0.45; }
.seg--current { background: var(--stage-accent); }
</style>
