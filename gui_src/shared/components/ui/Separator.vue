<script setup lang="ts">
/** Short hairline between groups of controls inside a chrome surface (DESIGN.md 8.11). */
withDefaults(defineProps<{ orientation?: 'vertical' | 'horizontal' }>(), { orientation: 'vertical' });
</script>

<template>
  <span
    class="shrink-0 bg-separator-strong"
    :class="orientation === 'vertical' ? 'mx-1 h-4 w-px' : 'my-1 h-px w-4'"
    aria-hidden="true"
  />
</template>
