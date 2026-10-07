<script setup lang="ts">
/**
 * Title row of a card (DESIGN.md 8.4): title on the left, optional meta text
 * after it, actions on the right. `level` picks Title 3 (cards) or
 * Headline (nested sections).
 */
withDefaults(defineProps<{
  title?: string;
  level?: 'card' | 'section';
}>(), { level: 'card' });
</script>

<template>
  <header class="flex items-center justify-between gap-3 min-h-[30px]">
    <div class="flex items-baseline gap-2 min-w-0 flex-1">
      <slot name="title">
        <h2 class="text-fg1 ellipsis" :class="level === 'card' ? 'type-title-3' : 'type-headline'">{{ title }}</h2>
      </slot>
      <span v-if="$slots.meta" class="type-footnote nums text-fg3 shrink-0"><slot name="meta" /></span>
    </div>
    <div v-if="$slots.actions" class="flex items-center gap-1 shrink-0">
      <slot name="actions" />
    </div>
  </header>
</template>
