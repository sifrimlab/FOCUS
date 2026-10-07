<script setup lang="ts">
/** Placeholder for an empty list (DESIGN.md 8.13): dashed well, icon, text. */
import AppIcon from './AppIcon.vue';
import type { IconName } from '../../icons/paths';

defineProps<{ icon?: IconName }>();
</script>

<template>
  <div class="empty rounded-well flex flex-col items-center justify-center gap-2 px-6 py-7 text-center">
    <AppIcon v-if="icon" :name="icon" :size="20" class="text-fg3" />
    <p class="type-callout text-fg3"><slot /></p>
  </div>
</template>

<style scoped>
.empty {
  outline: 1.5px dashed var(--separator-strong);
  outline-offset: -1.5px;
}
</style>
