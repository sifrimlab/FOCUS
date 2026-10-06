<script setup lang="ts">
/** Guided configuration builder: Samples, Modalities, Settings, Review. */
import type { Component } from 'vue';
import { useBuilderStore, type BuilderStep } from '../store/builder';
import BuilderHeader from '../components/builder/BuilderHeader.vue';
import BuilderNav from '../components/builder/BuilderNav.vue';
import SamplesStep from '../components/builder/steps/SamplesStep.vue';
import ModalitiesStep from '../components/builder/steps/ModalitiesStep.vue';
import ModalitySettingsStep from '../components/builder/steps/ModalitySettingsStep.vue';
import ReviewStep from '../components/builder/steps/ReviewStep.vue';

const STEP_VIEWS: Record<BuilderStep, Component> = {
  samples: SamplesStep,
  modalities: ModalitiesStep,
  settings: ModalitySettingsStep,
  review: ReviewStep,
};

const builder = useBuilderStore();
</script>

<template>
  <div class="mx-auto flex max-w-3xl flex-col gap-6 px-4 pt-4 pb-4">
    <BuilderHeader />
    <Transition name="view" mode="out-in">
      <component :is="STEP_VIEWS[builder.step]" :key="builder.step" class="pt-4" />
    </Transition>
    <BuilderNav />
  </div>
</template>
