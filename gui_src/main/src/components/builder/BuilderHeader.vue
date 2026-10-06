<script setup lang="ts">
/** Builder header: the dataset in use and the builder menu (change dataset, reset). */
import { useMainStore } from '../../store/main';
import { useDialog } from '../../composables/useDialog';
import OverflowMenu, { type MenuItem } from '../ui/OverflowMenu.vue';

const store = useMainStore();
const { showConfirm } = useDialog();

const confirmReset = async () => {
  const ok = await showConfirm({
    message: 'Reset all configuration? This cannot be undone.',
    confirmLabel: 'Reset',
    variant: 'danger',
  });
  if (ok) store.resetAll();
};

const MENU: MenuItem[] = [
  { label: 'Change dataset', icon: 'folder-open', action: () => store.goToSetup() },
  { label: 'Reset configuration', icon: 'arrow-path', tone: 'danger', action: confirmReset },
];
</script>

<template>
  <div class="flex items-center justify-between gap-3">
    <div class="flex min-w-0 items-baseline gap-2">
      <span class="type-footnote text-fg3 shrink-0">Dataset</span>
      <code class="type-mono-small text-fg2 ellipsis" :title="store.config.dataset_path">{{ store.config.dataset_path }}</code>
    </div>
    <OverflowMenu :items="MENU" label="Configuration menu" />
  </div>
</template>
