<script setup lang="ts">
/**
 * Top-level content surface (DESIGN.md 8.4): content material, card radius.
 * `hero` uses the larger dialog radius for the single focal card of a view.
 * Never nest a GlassCard in another one; nest an InsetWell instead.
 */
withDefaults(defineProps<{
  as?: string;
  hero?: boolean;
  padding?: 'none' | 'md' | 'lg';
}>(), { as: 'section', padding: 'md' });
</script>

<template>
  <component
    :is="as"
    class="material-content"
    :class="[hero ? 'rounded-dialog' : 'rounded-card', { 'p-5': padding === 'md', 'p-7': padding === 'lg' }]"
  >
    <slot />
  </component>
</template>
