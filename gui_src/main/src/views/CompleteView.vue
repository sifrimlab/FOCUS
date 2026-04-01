<script setup lang="ts">
import { useMainStore } from '../store/main';
import OutputSummary from '../components/OutputSummary.vue';

const store = useMainStore();
</script>

<template>
  <div class="max-w-3xl mx-auto px-4 py-12">
    <!-- Success banner -->
    <div class="bg-green-50 dark:bg-green-900/20 border border-green-400 dark:border-green-700 rounded-lg p-8 text-center mb-8">
      <div class="text-5xl mb-4">&#10003;</div>
      <h1 class="text-2xl font-bold text-green-700 dark:text-green-400 mb-2">Pipeline Completed</h1>
      <p class="text-gray-600 dark:text-gray-300">All processing stages have finished successfully.</p>
    </div>

    <!-- Output files -->
    <OutputSummary :files="store.pipelineStatus.output_files" />

    <!-- Start again -->
    <div class="text-center mt-8">
      <button
        @click="store.resetAll()"
        class="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-bold"
      >
        Start New Project
      </button>
    </div>
  </div>
</template>
