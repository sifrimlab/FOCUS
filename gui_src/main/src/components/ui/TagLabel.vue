<script setup lang="ts">
/** Read-only pill naming a category next to a title (e.g. a modality type). */
</script>

<template>
  <span class="tag type-caption text-fg2"><slot /></span>
</template>

<style scoped>
.tag {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  height: 20px;
  padding: 0 8px;
  border-radius: var(--radius-full);
  background: var(--mat-inset-fill);
  white-space: nowrap;
}
</style>
