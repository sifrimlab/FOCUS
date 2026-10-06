<script setup lang="ts">
/**
 * One stage in the rail: status mark in the stage's own color, name, duration.
 * The active (or failed) stage expands to list its modalities.
 */
import type { StageProgress } from '../../composables/useRunProgress';
import { formatDuration } from '../../utils/duration';
import AppIcon from '../ui/AppIcon.vue';

defineProps<{ stage: StageProgress; index: number }>();
</script>

<template>
  <li :data-stage="stage.id" class="flex flex-col gap-1" :aria-current="stage.state === 'active' ? 'step' : undefined">
    <div class="flex items-center gap-3 rounded-row px-2 py-2" :class="{ 'item--active': stage.state === 'active' }">
      <span class="mark" :class="`mark--${stage.state}`" aria-hidden="true">
        <AppIcon v-if="stage.state === 'done'" name="check" :size="12" />
        <AppIcon v-else-if="stage.state === 'failed'" name="x-mark" :size="12" />
        <span v-else class="type-caption nums">{{ index + 1 }}</span>
      </span>
      <span class="type-body-strong flex-1" :class="stage.state === 'pending' ? 'text-fg3' : 'text-fg1'">{{ stage.label }}</span>
      <span v-if="stage.seconds !== null" class="type-footnote nums text-fg3">{{ formatDuration(stage.seconds) }}</span>
    </div>

    <ul
      v-if="(stage.state === 'active' || stage.state === 'failed') && stage.modalities.length"
      class="ml-[21px] flex flex-col gap-0.5 border-l border-separator pl-4"
    >
      <li v-for="m in stage.modalities" :key="m.name" class="flex items-center gap-3 py-1">
        <span class="sub" :class="`sub--${m.state}`" aria-hidden="true" />
        <span class="type-callout flex-1 ellipsis" :class="m.state === 'pending' ? 'text-fg3' : 'text-fg1'" :title="m.name">
          {{ m.name }}
        </span>
        <span v-if="m.seconds !== null" class="type-footnote nums text-fg3">{{ formatDuration(m.seconds) }}</span>
      </li>
    </ul>
  </li>
</template>

<style scoped>
.item--active { background: var(--mat-inset-fill); }

.mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-full);
  transition: background-color var(--dur-base) var(--ease-out), color var(--dur-base) var(--ease-out);
}
.mark--pending { box-shadow: inset 0 0 0 1.5px var(--stage-accent); color: var(--fg3); opacity: 0.7; }
.mark--active  { background: var(--stage-accent); color: var(--stage-on-accent); animation: focus-breathe 2.4s var(--ease-in-out) infinite; }
.mark--done    { background: var(--stage-accent); color: var(--stage-on-accent); }
.mark--failed  { background: var(--danger); color: var(--fg-inverse); }

.sub {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: var(--radius-full);
  box-shadow: inset 0 0 0 1.5px var(--separator-strong);
}
.sub--done   { background: var(--stage-accent); box-shadow: none; }
.sub--active { background: var(--stage-accent); box-shadow: none; animation: focus-breathe 2.4s var(--ease-in-out) infinite; }

@media (prefers-reduced-motion: reduce) {
  .mark--active, .sub--active { animation: none; }
}
</style>
