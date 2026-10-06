<script setup lang="ts">
/**
 * Step 3: settings of one modality at a time. The reference shows only
 * preprocessing; other modalities also show alignment and registration.
 * Showing a modality marks it as visited.
 */
import { computed, watch } from 'vue';
import { useMainStore } from '../../../store/main';
import { useBuilderStore } from '../../../store/builder';
import StepFrame from '../StepFrame.vue';
import Banner from '../../ui/Banner.vue';
import ModalitySwitcher from '../settings/ModalitySwitcher.vue';
import PreprocessingSection from '../settings/PreprocessingSection.vue';
import AlignmentSection from '../settings/AlignmentSection.vue';
import RegistrationSection from '../settings/RegistrationSection.vue';

const store = useMainStore();
const builder = useBuilderStore();

const index = computed(() => builder.activeModality);
const modality = computed(() => store.config.modalities[index.value]);
const isReference = computed(() => modality.value?.name === store.config.reference_modality);

watch(() => modality.value?.name, name => { if (name) builder.markVisited(name); }, { immediate: true });
</script>

<template>
  <StepFrame
    title="Modality settings"
    description="Review the settings of each modality. Defaults work for most datasets; Next moves to the following modality."
  >
    <ModalitySwitcher />

    <Transition name="fade" mode="out-in">
      <div v-if="modality" :key="modality.name" class="flex flex-col gap-6">
        <PreprocessingSection :index="index" />
        <Banner v-if="isReference" tone="info" title="Reference modality">
          The other modalities are aligned and registered onto {{ modality.name }}, so it has no alignment or
          registration settings.
        </Banner>
        <template v-else>
          <AlignmentSection :index="index" />
          <RegistrationSection :index="index" />
        </template>
      </div>
    </Transition>
  </StepFrame>
</template>
