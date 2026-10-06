<script setup lang="ts">
/**
 * Floating sticky action bar (DESIGN.md 8.12): chrome material, pinned
 * 16px above the bottom of its scroll container. Slots: start, end.
 */
</script>

<template>
  <div class="sticky bottom-4 z-10">
    <div class="material-chrome rounded-card flex items-center justify-between gap-3 p-3">
      <div class="flex items-center gap-2"><slot name="start" /></div>
      <div class="flex items-center gap-2"><slot name="end" /></div>
    </div>
  </div>
</template>
