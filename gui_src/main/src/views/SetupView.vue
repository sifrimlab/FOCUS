<script setup lang="ts">
/**
 * Setup: choose the dataset, confirm samples, then decide where the config
 * comes from (resume the existing one, upload a file, or start empty).
 */
import { ref } from 'vue';
import { useMainStore } from '../store/main';
import BrandLockup from '@focus/ui/components/shell/BrandLockup.vue';
import DatasetPathCard from '../components/setup/DatasetPathCard.vue';
import SamplesFoundCard from '../components/setup/SamplesFoundCard.vue';
import ExistingConfigCard from '../components/setup/ExistingConfigCard.vue';
import CorruptedConfigCard from '../components/setup/CorruptedConfigCard.vue';
import ConfigImportCard from '../components/setup/ConfigImportCard.vue';

type Step = 'path' | 'samples' | 'existing' | 'corrupted' | 'import';

const store = useMainStore();
const step = ref<Step>('path');
const importReturnStep = ref<Step>('samples');
const corruptedErrors = ref<string[]>([]);

/** Open the import step, remembering where Back should return to. */
const toImport = (from: Step) => {
  importReturnStep.value = from;
  step.value = 'import';
};

const confirmSamples = () => {
  if (store.hasExistingConfig) step.value = 'existing';
  else toImport('samples');
};

const loadExisting = async () => {
  const result = await store.loadExistingConfig();
  if (result.success) {
    store.goToConfig();
  } else {
    corruptedErrors.value = result.errors ?? ['Unknown error reading config file.'];
    step.value = 'corrupted';
  }
};
</script>

<template>
  <div class="flex min-h-full flex-col items-center justify-center px-4 py-16">
    <div class="w-full max-w-lg">
      <header class="mb-12 flex flex-col items-center gap-3 text-center">
        <BrandLockup />
        <p class="type-callout text-fg3">FOCUS: End-to-end preprocessing, alignment, and registration pipeline</p>
      </header>

      <Transition name="fade" mode="out-in">
        <DatasetPathCard v-if="step === 'path'" @continue="step = 'samples'" />
        <SamplesFoundCard v-else-if="step === 'samples'" @confirm="confirmSamples" @back="step = 'path'" />
        <ExistingConfigCard
          v-else-if="step === 'existing'"
          @load="loadExisting"
          @fresh="toImport('existing')"
          @back="step = 'samples'"
        />
        <CorruptedConfigCard
          v-else-if="step === 'corrupted'"
          :errors="corruptedErrors"
          @fresh="toImport('corrupted')"
          @back="step = 'existing'"
        />
        <ConfigImportCard v-else @done="store.goToConfig($event)" @back="step = importReturnStep" />
      </Transition>
    </div>
  </div>
</template>
