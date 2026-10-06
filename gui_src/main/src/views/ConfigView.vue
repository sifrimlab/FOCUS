<script setup lang="ts">
/** Configuration builder: settings cards plus the floating action bar. */
import { useMainStore } from '../store/main';
import { useDialog } from '../composables/useDialog';
import { pluralize } from '../utils/format';
import PageHeader from '../components/ui/PageHeader.vue';
import ActionBar from '../components/ui/ActionBar.vue';
import BaseButton from '../components/ui/BaseButton.vue';
import PipelineSettingsCard from '../components/config/PipelineSettingsCard.vue';
import AnnotationsCard from '../components/config/AnnotationsCard.vue';
import ModalitiesCard from '../components/config/ModalitiesCard.vue';
import SamplesCard from '../components/config/SamplesCard.vue';
import ValidationErrors from '../components/config/ValidationErrors.vue';

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
</script>

<template>
  <div class="mx-auto flex max-w-4xl flex-col gap-4 px-4 pt-16 pb-4">
    <PageHeader title="Configuration builder" class="mb-4">
      Dataset: <code class="type-mono-small rounded-tiny bg-inset px-1 text-fg2">{{ store.config.dataset_path }}</code>
      <span class="nums"> · {{ pluralize(store.samples.length, 'sample') }} found</span>
    </PageHeader>

    <PipelineSettingsCard />
    <AnnotationsCard />
    <ModalitiesCard />
    <SamplesCard />

    <Transition name="fade">
      <ValidationErrors v-if="store.validationErrors.length > 0" :errors="store.validationErrors" />
    </Transition>

    <ActionBar>
      <template #start>
        <BaseButton size="lg" icon="arrow-left" @click="store.goToSetup()">Back</BaseButton>
        <BaseButton size="lg" variant="destructive" icon="arrow-path" @click="confirmReset">Reset</BaseButton>
      </template>
      <template #end>
        <BaseButton variant="success" size="xl" icon="play" :disabled="store.isLoading" @click="store.startPipeline()">
          {{ store.isLoading ? 'Validating…' : 'Start processing' }}
        </BaseButton>
      </template>
    </ActionBar>
  </div>
</template>
