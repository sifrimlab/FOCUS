<script setup lang="ts">
/** Compact tracker for up to nine named sub-steps (DESIGN.md 8.7). */
import { computed } from 'vue';
import AppIcon from '../ui/AppIcon.vue';

const props = defineProps<{
  total: number;
  current: number;
}>();

const steps = computed(() => Array.from({ length: props.total }, (_, i) => i + 1));

// Fraction of the line between the first and the current dot.
const progress = computed(() => {
  if (props.total <= 1 || props.current <= 1) return 0;
  return Math.min(1, (props.current - 1) / (props.total - 1));
});

const status = (n: number) => (n < props.current ? 'done' : n === props.current ? 'active' : 'pending');
</script>

<template>
  <div class="relative flex items-center justify-between px-3" role="list" :aria-label="`Step ${current} of ${total}`">
    <div class="track" aria-hidden="true">
      <div class="track__fill" :style="{ transform: `scaleX(${progress})` }" />
    </div>
    <span
      v-for="n in steps"
      :key="n"
      role="listitem"
      class="dot type-caption nums"
      :class="`dot--${status(n)}`"
    >
      <AppIcon v-if="status(n) === 'done'" name="check" :size="12" />
      <span v-else>{{ n }}</span>
    </span>
  </div>
</template>

<style scoped>
.track {
  position: absolute;
  left: 24px;
  right: 24px;
  top: 50%;
  height: 2px;
  margin-top: -1px;
  border-radius: var(--radius-full);
  background: var(--separator-strong);
}
.track__fill {
  height: 100%;
  border-radius: inherit;
  background: var(--success);
  transform-origin: left center;
  transition: transform var(--dur-slow) var(--ease-out);
}
.dot {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-full);
  transition:
    background-color var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-spring-snappy);
}
.dot--done    { background: var(--success); color: var(--fg-inverse); }
.dot--active  { background: var(--primary-fill); color: var(--fg-inverse); transform: scale(1.12); }
.dot--pending { background: var(--mat-content-fill); color: var(--fg3); box-shadow: inset 0 0 0 1.5px var(--separator-strong); }
</style>
