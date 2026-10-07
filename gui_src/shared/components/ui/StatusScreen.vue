<script setup lang="ts">
/**
 * Full-screen state (DESIGN.md 8.19): one hero card centered over the
 * ambient backdrop, for loading, finished, error and offline states.
 * `progress` adds a progress bar (null = indeterminate); `detail` shows a
 * verbatim message (error text) in an inset well. Slot: footer (actions).
 */
import AppIcon from './AppIcon.vue';
import GlassCard from './GlassCard.vue';
import InsetWell from './InsetWell.vue';
import ProgressBar from './ProgressBar.vue';
import type { IconName } from '../../icons/paths';

defineProps<{
  tone: 'primary' | 'success' | 'warning' | 'danger';
  icon: IconName;
  title: string;
  message?: string;
  meta?: string;
  detail?: string;
  progress?: number | null;
}>();
</script>

<template>
  <main class="grid min-h-screen place-items-center p-6">
    <GlassCard hero padding="lg" class="flex w-full max-w-md flex-col items-center gap-3 text-center" role="status">
      <span class="badge" :class="`badge--${tone}`"><AppIcon :name="icon" :size="24" /></span>
      <h1 class="type-title-1 text-fg1">{{ title }}</h1>
      <p v-if="message" class="type-body text-fg2">{{ message }}</p>
      <p v-if="meta" class="type-footnote nums text-fg3">{{ meta }}</p>
      <InsetWell v-if="detail" class="type-mono-small w-full text-left text-fg2 wrap-anywhere">{{ detail }}</InsetWell>
      <ProgressBar v-if="progress !== undefined" :value="progress" :label="title" class="mt-1" />
      <slot name="footer" />
    </GlassCard>
  </main>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  margin-bottom: 4px;
  border-radius: var(--radius-full);
}
.badge--primary { background: var(--primary-soft); color: var(--primary); }
.badge--success { background: var(--success-soft); color: var(--success); }
.badge--warning { background: var(--warning-soft); color: var(--warning); }
.badge--danger  { background: var(--danger-soft);  color: var(--danger); }
</style>
