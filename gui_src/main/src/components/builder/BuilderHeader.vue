<script setup lang="ts">
/**
 * Sticky glass header of the builder, as wide as the content column:
 * the dataset in use with the builder menu (change dataset, reset) on top,
 * the step indicator spread across the full width below.
 */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import { useBuilderStore, BUILDER_STEPS } from '../../store/builder';
import { useDialog } from '../../composables/useDialog';
import AppIcon from '@focus/ui/components/ui/AppIcon.vue';
import Stepper from '@focus/ui/components/ui/Stepper.vue';
import OverflowMenu, { type MenuItem } from '@focus/ui/components/ui/OverflowMenu.vue';

const store = useMainStore();
const builder = useBuilderStore();
const { showConfirm } = useDialog();

const steps = computed(() => BUILDER_STEPS.map((s, i) => ({ ...s, state: builder.stepStates[i]! })));

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
  <header class="material-chrome rounded-card sticky top-4 z-header flex flex-col">
    <div class="flex items-center gap-3 py-2 pr-2 pl-4">
      <span class="badge" aria-hidden="true"><AppIcon name="folder" /></span>
      <div class="flex min-w-0 flex-1 flex-col">
        <span class="type-caption text-fg3">Dataset</span>
        <code class="type-mono-small text-fg1 ellipsis" :title="store.config.dataset_path">{{ store.config.dataset_path }}</code>
      </div>
      <OverflowMenu :items="MENU" label="Configuration menu" />
    </div>
    <div class="border-t border-separator p-1.5">
      <Stepper :steps="steps" label="Configuration steps" stretch @select="builder.goTo($event)" />
    </div>
  </header>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  background: var(--primary-soft);
  color: var(--primary);
}
</style>
