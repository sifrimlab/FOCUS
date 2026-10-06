<script setup lang="ts">
/**
 * Pipeline stage stepper (DESIGN.md 8.7): done stages show a check on
 * success, the active stage breathes, pending stages are inset.
 */
import { computed } from 'vue';
import AppIcon from '../ui/AppIcon.vue';

export interface Stage {
  name: string;
  label: string;
}

const props = defineProps<{
  currentStage: string | null;
  stages: Stage[];
}>();

const activeIndex = computed(() => {
  const idx = props.stages.findIndex(s => s.name === props.currentStage);
  return idx === -1 ? 0 : idx;
});

const status = (i: number) => (i < activeIndex.value ? 'done' : i === activeIndex.value ? 'active' : 'pending');
</script>

<template>
  <ol class="flex w-full items-start" :class="stages.length === 1 ? 'justify-center' : 'justify-between'">
    <template v-for="(stage, i) in stages" :key="stage.name">
      <li class="flex w-24 flex-col items-center gap-1.5" :aria-current="status(i) === 'active' ? 'step' : undefined">
        <span class="circle type-body-strong nums" :class="`circle--${status(i)}`">
          <AppIcon v-if="status(i) === 'done'" name="check" />
          <span v-else>{{ i + 1 }}</span>
        </span>
        <span class="type-caption text-center" :class="status(i) === 'active' ? 'text-primary-fg' : 'text-fg3'">
          {{ stage.label }}
        </span>
      </li>
      <li v-if="i < stages.length - 1" class="connector" :class="{ 'connector--done': i < activeIndex }" aria-hidden="true" />
    </template>
  </ol>
</template>

<style scoped>
.circle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  transition: background-color var(--dur-base) var(--ease-out), color var(--dur-base) var(--ease-out);
}
.circle--done    { background: var(--success); color: var(--fg-inverse); }
.circle--active  { background: var(--primary-fill); color: var(--fg-inverse); animation: focus-breathe 2.4s var(--ease-in-out) infinite; }
.circle--pending { background: var(--mat-inset-fill); color: var(--fg3); }

.connector {
  flex: 1;
  height: 2px;
  margin-top: 15px;
  border-radius: var(--radius-full);
  background: var(--separator-strong);
  transition: background-color var(--dur-slow) var(--ease-out);
}
.connector--done { background: var(--success); }

@media (prefers-reduced-motion: reduce) {
  .circle--active { animation: none; box-shadow: 0 0 0 4px var(--primary-soft); }
}
</style>
