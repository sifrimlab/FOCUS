<script setup lang="ts">
/**
 * Floating chrome surface (DESIGN.md 4.1, 8.11): chrome material in a pill
 * (clusters, toolbars) or panel (action bar, layer panels) shape. Callers
 * position it and lay out its children with layout utilities only.
 */
withDefaults(defineProps<{
  as?: string;
  shape?: 'pill' | 'panel';
  padding?: 'sm' | 'md';
}>(), { as: 'div', shape: 'pill', padding: 'sm' });
</script>

<template>
  <component
    :is="as"
    class="material-chrome"
    :class="[shape === 'pill' ? 'rounded-full' : 'rounded-card', padding === 'sm' ? 'p-1' : 'p-3']"
  >
    <slot />
  </component>
</template>
