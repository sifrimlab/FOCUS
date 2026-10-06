<script setup lang="ts">
/**
 * One activity line in the order [step] [sample] [modality] [stage]: a step
 * that started (or moved to a new sample), a finished step with its duration,
 * or an error. Renders two grid cells (time, content) for ActivityLog's grid.
 */
import { computed } from 'vue';
import type { ActivityEntry } from '../../api/types';
import { STAGE_LABELS, type StageId } from '../../utils/runPlan';
import { formatClock, formatDuration } from '../../utils/duration';
import AppIcon from '../ui/AppIcon.vue';
import TagLabel from '../ui/TagLabel.vue';

const props = defineProps<{ entry: ActivityEntry; latest: boolean }>();

const stageLabel = computed(() => {
  const id = props.entry.stage;
  return id ? (STAGE_LABELS[id as StageId] ?? id) : null;
});
const done = computed(() => props.entry.kind === 'done');
const duration = computed(() => {
  const s = props.entry.seconds ?? 0;
  return s < 1 ? '<1s' : formatDuration(s);
});
</script>

<template>
  <time class="type-mono-small text-fg3 pt-0.5">{{ formatClock(entry.t) }}</time>
  <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
    <AppIcon v-if="done" name="check" :size="12" class="text-fg3" />
    <AppIcon v-else-if="entry.kind === 'error'" name="exclamation-circle" class="text-danger-fg" />
    <span v-if="entry.step" class="type-callout" :class="done ? 'text-fg3' : latest ? 'text-fg1' : 'text-fg2'">
      <span v-if="entry.step_total" class="type-footnote nums text-fg3">{{ entry.step_index }}/{{ entry.step_total }}</span>
      {{ entry.step }}
    </span>
    <span v-if="done" class="type-footnote nums text-fg3">{{ duration }}</span>
    <span v-if="entry.kind === 'error'" class="type-callout text-danger-fg break-words">{{ entry.text }}</span>
    <span v-if="entry.sample" class="type-mono-small text-fg2">{{ entry.sample }}</span>
    <TagLabel v-if="entry.modality">{{ entry.modality }}</TagLabel>
    <span v-if="stageLabel && !done" :data-stage="entry.stage" class="stage type-footnote text-fg3">
      <span class="stage__dot" aria-hidden="true" />{{ stageLabel }}
    </span>
    <TagLabel v-if="entry.cached">cached</TagLabel>
  </div>
</template>

<style scoped>
.stage {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.stage__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--stage-accent);
}
</style>
