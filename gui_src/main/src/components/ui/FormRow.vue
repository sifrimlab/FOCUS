<script setup lang="ts">
/**
 * One label/control row in a settings form: label (and optional hint) on
 * the left, control on the right. `disabled` dims the whole row.
 */
defineProps<{
  label: string;
  hint?: string;
  disabled?: boolean;
}>();
</script>

<template>
  <div class="flex items-center justify-between gap-4 min-h-[30px]" :class="{ 'opacity-40': disabled }">
    <div class="min-w-0">
      <span class="type-body-strong text-fg2 block">{{ label }}</span>
      <span v-if="hint" class="type-footnote text-fg3 block">{{ hint }}</span>
    </div>
    <slot />
  </div>
</template>
