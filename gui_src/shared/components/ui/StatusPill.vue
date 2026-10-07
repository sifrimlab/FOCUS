<script setup lang="ts">
/** Small pill with a live dot (DESIGN.md 8.6), e.g. the run state. */
withDefaults(defineProps<{
  live?: boolean;
  tone?: 'primary' | 'stage' | 'warning' | 'danger' | 'success';
}>(), { live: true, tone: 'primary' });
</script>

<template>
  <span class="pill material-chrome type-caption text-fg1">
    <span class="pill__dot" :class="[`pill__dot--${tone}`, { 'pill__dot--live': live }]" aria-hidden="true" />
    <slot />
  </span>
</template>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: var(--control-sm);
  padding: 0 10px;
  border-radius: var(--radius-full);
}
.pill__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--primary);
}
.pill__dot--stage   { background: var(--stage-accent); }
.pill__dot--warning { background: var(--warning); }
.pill__dot--danger  { background: var(--danger); }
.pill__dot--success { background: var(--success); }
.pill__dot--live { animation: focus-breathe 2.4s var(--ease-in-out) infinite; }

@media (prefers-reduced-motion: reduce) {
  .pill__dot--live { animation: none; }
}
</style>
