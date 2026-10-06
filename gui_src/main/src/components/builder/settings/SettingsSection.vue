<script setup lang="ts">
/** One numbered section of a modality's settings (Preprocessing, Alignment, Registration). */
import GlassCard from '../../ui/GlassCard.vue';

defineProps<{
  number: number;
  title: string;
  description?: string;
}>();
</script>

<template>
  <GlassCard class="flex flex-col gap-5" :aria-label="title">
    <header class="flex items-start gap-3">
      <span class="num type-caption nums" aria-hidden="true">{{ number }}</span>
      <div class="flex min-w-0 flex-1 flex-col gap-0.5">
        <h2 class="type-title-3 text-fg1">{{ title }}</h2>
        <p v-if="description" class="type-footnote text-fg3">{{ description }}</p>
      </div>
      <slot name="aside" />
    </header>
    <slot />
  </GlassCard>
</template>

<style scoped>
.num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  margin-top: -1px;
  border-radius: var(--radius-full);
  background: var(--primary-soft);
  color: var(--primary-fg);
}
</style>
