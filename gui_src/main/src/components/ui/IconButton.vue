<script setup lang="ts">
/**
 * Square icon-only button or link (DESIGN.md 8.1). `label` is required:
 * it becomes the accessible name and the tooltip.
 */
import AppIcon from './AppIcon.vue';
import type { IconName } from '../../icons/paths';

withDefaults(defineProps<{
  icon: IconName;
  label: string;
  size?: 'sm' | 'md';
  tone?: 'neutral' | 'primary' | 'danger';
  round?: boolean;
  href?: string;
}>(), { size: 'md', tone: 'neutral' });
</script>

<template>
  <component
    :is="href ? 'a' : 'button'"
    :href="href"
    :type="href ? undefined : 'button'"
    :target="href ? '_blank' : undefined"
    :rel="href ? 'noopener noreferrer' : undefined"
    :aria-label="label"
    :title="label"
    class="icon-btn"
    :class="[`icon-btn--${size}`, `icon-btn--${tone}`, { 'icon-btn--round': round }]"
  >
    <AppIcon :name="icon" :size="size === 'sm' ? 16 : 20" />
  </component>
</template>

<style scoped>
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  color: var(--fg3);
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-base) var(--ease-spring-snappy);
}
.icon-btn:hover:not(:disabled) { background: var(--mat-inset-fill); color: var(--fg1); }
.icon-btn:active:not(:disabled) { transform: scale(var(--press-scale)); transition-duration: var(--dur-instant); }
.icon-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.icon-btn--sm { width: var(--control-sm); height: var(--control-sm); }
.icon-btn--md { width: var(--control-md); height: var(--control-md); }
.icon-btn--round { border-radius: var(--radius-full); }

.icon-btn--primary { color: var(--primary-fg); }
.icon-btn--primary:hover:not(:disabled) { background: var(--primary-soft); color: var(--primary-fg); }
.icon-btn--danger { color: var(--danger-fg); }
.icon-btn--danger:hover:not(:disabled) { background: var(--danger-soft); color: var(--danger-fg); }
</style>
