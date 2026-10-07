<script setup lang="ts">
/** Nested container inside a material (DESIGN.md 4.1, inset level). */
withDefaults(defineProps<{
  as?: string;
  padding?: 'none' | 'sm' | 'md';
}>(), { as: 'div', padding: 'md' });
</script>

<template>
  <component
    :is="as"
    class="material-inset rounded-well"
    :class="{ 'p-1': padding === 'sm', 'p-4': padding === 'md' }"
  >
    <slot />
  </component>
</template>
