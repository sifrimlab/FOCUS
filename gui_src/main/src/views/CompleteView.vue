<script setup lang="ts">
/** Complete: success header, output files, cleanup, and a fresh start. */
import { useMainStore } from '../store/main';
import CompletionHero from '../components/complete/CompletionHero.vue';
import OutputSummary from '../components/complete/OutputSummary.vue';
import CleanupCard from '../components/complete/CleanupCard.vue';
import BaseButton from '../components/ui/BaseButton.vue';

const store = useMainStore();
</script>

<template>
  <div class="flex flex-col items-center px-4 py-16">
    <div class="flex w-full max-w-2xl flex-col gap-5">
      <CompletionHero />
      <OutputSummary :files="store.pipelineStatus.output_files" />
      <CleanupCard :files="store.pipelineStatus.output_files" />
      <div class="flex justify-center pt-2">
        <BaseButton variant="primary" size="xl" icon="plus" @click="store.resetAll()">Start new project</BaseButton>
      </div>
    </div>
  </div>
</template>
