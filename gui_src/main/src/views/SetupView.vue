<script setup lang="ts">
/** Setup: choose the dataset, confirm samples, resolve an existing config. */
import { ref } from 'vue';
import { useMainStore } from '../store/main';
import BrandLockup from '../components/shell/BrandLockup.vue';
import DatasetPathCard from '../components/setup/DatasetPathCard.vue';
import SamplesFoundCard from '../components/setup/SamplesFoundCard.vue';
import ExistingConfigCard from '../components/setup/ExistingConfigCard.vue';
import CorruptedConfigCard from '../components/setup/CorruptedConfigCard.vue';

type Step = 'path' | 'samples' | 'existing' | 'corrupted';

const store = useMainStore();
const step = ref<Step>('path');
const corruptedErrors = ref<string[]>([]);

const confirmSamples = () => {
  if (store.hasExistingConfig) step.value = 'existing';
  else store.goToConfig();
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

const overwriteCorrupted = async () => {
  await store.autoSave();
  store.goToConfig();
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
        <ExistingConfigCard v-else-if="step === 'existing'" @load="loadExisting" @fresh="store.goToConfig()" />
        <CorruptedConfigCard
          v-else
          :errors="corruptedErrors"
          @overwrite="overwriteCorrupted"
          @back="step = 'path'"
        />
      </Transition>
    </div>
  </div>
</template>
