<script setup lang="ts">
/**
 * Progress track (DESIGN.md 8.7). `value` 0-100 is determinate; null shows
 * the indeterminate shimmer. `active` adds a soft sheen sweep while running.
 */
import { computed } from 'vue';

const props = withDefaults(defineProps<{
  value: number | null;
  tone?: 'primary' | 'data-2' | 'success' | 'stage';
  label: string;
  active?: boolean;
}>(), { tone: 'primary', active: true });

const clamped = computed(() => (props.value === null ? null : Math.min(100, Math.max(0, props.value))));
</script>

<template>
  <div
    class="track"
    role="progressbar"
    :aria-label="label"
    :aria-valuenow="clamped ?? undefined"
    aria-valuemin="0"
    aria-valuemax="100"
  >
    <div
      v-if="clamped !== null"
      class="fill"
      :class="[`fill--${tone}`, { 'fill--active': active }]"
      :style="{ transform: `scaleX(${clamped / 100})` }"
    />
    <div v-else class="shimmer" :class="`fill--${tone}`" />
  </div>
</template>

<style scoped>
.track {
  position: relative;
  width: 100%;
  height: 6px;
  overflow: hidden;
  border-radius: var(--radius-full);
  background: var(--mat-inset-fill);
}
.fill {
  position: absolute;
  inset: 0;
  transform-origin: left center;
  border-radius: inherit;
  transition: transform var(--dur-slow) var(--ease-out);
}
.fill--primary { background-color: var(--primary); }
.fill--data-2  { background-color: var(--data-2); }
.fill--success { background-color: var(--success); }
.fill--stage   { background-color: var(--stage-accent); }

/* Sheen sweeping along the filled part while work is running. */
.fill--active::after {
  content: "";
  position: absolute;
  inset: 0;
  width: 40%;
  background: linear-gradient(90deg, transparent, rgb(255 255 255 / 0.35), transparent);
  animation: focus-shimmer 2.4s linear infinite;
}

.shimmer {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 40%;
  border-radius: inherit;
  animation: focus-shimmer 1.6s var(--ease-in-out) infinite;
}

@media (prefers-reduced-motion: reduce) {
  .fill--active::after { animation: none; display: none; }
  .shimmer { animation: none; width: 100%; opacity: 0.5; }
}
</style>
