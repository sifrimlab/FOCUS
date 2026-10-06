<script setup lang="ts">
/** Sticky step indicator of the builder; completed and unlocked steps can be revisited. */
import { computed } from 'vue';
import { useBuilderStore, BUILDER_STEPS } from '../../store/builder';
import Stepper from '../ui/Stepper.vue';

const builder = useBuilderStore();

const steps = computed(() => BUILDER_STEPS.map((s, i) => ({ ...s, state: builder.stepStates[i]! })));
</script>

<template>
  <div class="sticky top-4 z-20 self-start">
    <div class="material-chrome rounded-full p-1">
      <Stepper :steps="steps" label="Configuration steps" @select="builder.goTo($event)" />
    </div>
  </div>
</template>
