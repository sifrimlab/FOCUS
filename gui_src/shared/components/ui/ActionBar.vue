<script setup lang="ts">
/**
 * Floating sticky action bar (DESIGN.md 8.12): chrome material, pinned
 * 16px above the bottom of its scroll container. Slots: start, end.
 */
import ChromeSurface from './ChromeSurface.vue';
</script>

<template>
  <div class="sticky bottom-hud z-sticky">
    <ChromeSurface shape="panel" padding="md" class="flex items-center justify-between gap-3">
      <div class="flex items-center gap-2"><slot name="start" /></div>
      <div class="flex items-center gap-2"><slot name="end" /></div>
    </ChromeSurface>
  </div>
</template>
