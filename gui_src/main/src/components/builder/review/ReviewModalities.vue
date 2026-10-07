<script setup lang="ts">
/** Modality rows on Review; each opens that modality's settings for a targeted edit. */
import { useMainStore } from '../../../store/main';
import { useBuilderStore } from '../../../store/builder';
import type { Modality } from '../../../api/types';
import AppIcon from '@focus/ui/components/ui/AppIcon.vue';

const store = useMainStore();
const builder = useBuilderStore();

const summary = (m: Modality): string => {
  const cfg = store.config;
  if (m.name === cfg.reference_modality) return 'Reference';
  const parts: string[] = [];
  if (cfg.perform_alignment && cfg.modalities.length >= 2) {
    parts.push(m.alignment_strategy === 'pre_aligned' ? 'Pre-aligned' : 'Manual alignment');
    if (cfg.perform_registration) {
      parts.push(m.registration_type === 'none' ? 'No registration' : store.displayName(m.registration_type));
    }
  }
  return parts.join(' · ') || 'Preprocessing only';
};
</script>

<template>
  <ul class="flex flex-col divide-y divide-separator">
    <li v-for="(m, i) in store.config.modalities" :key="m.name">
      <button
        type="button"
        class="row flex w-full items-center gap-3 rounded-row px-2 py-2.5 text-left"
        :aria-label="`Edit settings of ${m.name}`"
        @click="builder.openModality(i, true)"
      >
        <AppIcon
          :name="m.name === store.config.reference_modality ? 'star' : 'document-text'"
          :class="m.name === store.config.reference_modality ? 'text-warning' : 'text-fg3'"
        />
        <span class="type-body-strong text-fg1 w-40 ellipsis">{{ m.name }}</span>
        <span class="type-callout text-fg2 w-48 ellipsis">{{ store.displayName(m.type) }}</span>
        <span class="type-footnote text-fg3 flex-1 ellipsis">{{ summary(m) }}</span>
        <AppIcon name="chevron-right" class="text-fg3" />
      </button>
    </li>
  </ul>
</template>

<style scoped>
.row { transition: background-color var(--dur-fast) var(--ease-out); }
.row:hover { background: var(--mat-inset-fill); }
</style>
