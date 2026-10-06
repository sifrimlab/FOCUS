<script setup lang="ts">
/**
 * Tinted message block (DESIGN.md 8.5): soft role background, leading icon,
 * optional title, body slot, optional actions slot.
 */
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';
import type { IconName } from '../../icons/paths';

export type BannerTone = 'info' | 'success' | 'warning' | 'danger' | 'neutral';

const props = withDefaults(defineProps<{
  tone?: BannerTone;
  title?: string;
  icon?: IconName;
  standalone?: boolean;
}>(), { tone: 'info' });

const DEFAULT_ICONS: Record<BannerTone, IconName> = {
  info: 'information-circle',
  neutral: 'information-circle',
  success: 'check-circle',
  warning: 'exclamation-triangle',
  danger: 'exclamation-circle',
};

const iconName = computed(() => props.icon ?? DEFAULT_ICONS[props.tone]);
</script>

<template>
  <div
    class="banner flex items-start gap-3"
    :class="[`banner--${tone}`, standalone ? 'rounded-card p-5' : 'rounded-well px-4 py-3']"
    :role="tone === 'danger' ? 'alert' : 'status'"
  >
    <AppIcon :name="iconName" :size="standalone ? 20 : 16" class="banner__icon mt-0.5" />
    <div class="min-w-0 flex-1">
      <p v-if="title" class="type-body-strong text-fg1">{{ title }}</p>
      <div class="type-callout text-fg2" :class="{ 'mt-0.5': title }"><slot /></div>
      <div v-if="$slots.actions" class="mt-3 flex flex-wrap items-center gap-2"><slot name="actions" /></div>
    </div>
  </div>
</template>

<style scoped>
.banner--info    { background: var(--primary-soft); }
.banner--success { background: var(--success-soft); }
.banner--warning { background: var(--warning-soft); }
.banner--danger  { background: var(--danger-soft); }
.banner--neutral { background: var(--mat-inset-fill); }

.banner--info    .banner__icon { color: var(--primary); }
.banner--success .banner__icon { color: var(--success); }
.banner--warning .banner__icon { color: var(--warning); }
.banner--danger  .banner__icon { color: var(--danger); }
.banner--neutral .banner__icon { color: var(--fg3); }
</style>
