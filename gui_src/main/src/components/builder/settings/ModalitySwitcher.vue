<script setup lang="ts">
/**
 * Tabs for the modalities in the Settings step: reference marked with a
 * star, visited ones with a check, the active one highlighted.
 */
import { useMainStore } from '../../../store/main';
import { useBuilderStore } from '../../../store/builder';
import AppIcon from '../../ui/AppIcon.vue';

const store = useMainStore();
const builder = useBuilderStore();
</script>

<template>
  <div role="tablist" aria-label="Modality" class="flex flex-wrap gap-1.5">
    <button
      v-for="(m, i) in store.config.modalities"
      :key="m.name"
      type="button"
      role="tab"
      class="tab type-body-strong"
      :aria-selected="i === builder.activeModality"
      @click="builder.activeModality = i"
    >
      <AppIcon v-if="m.name === store.config.reference_modality" name="star" :size="12" class="tab__star" />
      <span>{{ m.name }}</span>
      <AppIcon v-if="builder.visited.includes(m.name)" name="check" :size="12" class="tab__check" />
    </button>
  </div>
</template>

<style scoped>
.tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: var(--control-md);
  padding: 0 12px;
  border-radius: var(--radius-full);
  color: var(--fg2);
  background: var(--mat-inset-fill);
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.tab:hover { background: var(--mat-inset-hover); color: var(--fg1); }
.tab[aria-selected="true"] {
  background: var(--primary-fill);
  color: var(--fg-inverse);
  box-shadow: var(--fill-highlight), var(--elev-1);
}
.tab__star { color: var(--warning); }
.tab[aria-selected="true"] .tab__star,
.tab[aria-selected="true"] .tab__check { color: var(--fg-inverse); }
.tab__check { color: var(--success); }
</style>
