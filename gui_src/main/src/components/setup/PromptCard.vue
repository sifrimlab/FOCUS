<script setup lang="ts">
/**
 * Hero card for a Setup step: tinted status icon, Title 2, description,
 * body slot, and an actions row (primary first, full width).
 */
import GlassCard from '../ui/GlassCard.vue';
import AppIcon from '../ui/AppIcon.vue';
import type { IconName } from '../../icons/paths';

defineProps<{
  title: string;
  description?: string;
  icon?: IconName;
  tone?: 'primary' | 'success' | 'warning' | 'danger';
}>();
</script>

<template>
  <GlassCard hero padding="lg" class="flex flex-col gap-5">
    <div class="flex items-start gap-3">
      <span v-if="icon" class="badge mt-0.5" :class="`badge--${tone ?? 'primary'}`">
        <AppIcon :name="icon" />
      </span>
      <div class="min-w-0">
        <h2 class="type-title-2 text-fg1"><slot name="title">{{ title }}</slot></h2>
        <p v-if="description" class="type-callout text-fg3 mt-1">{{ description }}</p>
      </div>
    </div>
    <slot />
    <div class="flex gap-3"><slot name="actions" /></div>
  </GlassCard>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-full);
}
.badge--primary { background: var(--primary-soft); color: var(--primary); }
.badge--success { background: var(--success-soft); color: var(--success); }
.badge--warning { background: var(--warning-soft); color: var(--warning); }
.badge--danger  { background: var(--danger-soft);  color: var(--danger); }
</style>
