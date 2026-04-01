<script setup lang="ts">
import { useMainStore } from '../store/main';
import StageProgress from '../components/StageProgress.vue';

const store = useMainStore();

const openAlignmentTool = () => {
  window.open(`http://localhost:${store.pipelineStatus.alignment_port}`, '_blank');
};
</script>

<template>
  <div class="max-w-3xl mx-auto px-4 py-12">
    <h1 class="text-2xl font-bold text-center mb-8">Pipeline Execution</h1>

    <!-- Stage progress -->
    <div class="mb-10">
      <StageProgress
        :current-stage-index="store.pipelineStatus.stage_index"
        :total-stages="store.pipelineStatus.total_stages"
      />
    </div>

    <!-- Status card -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow p-6 border border-gray-200 dark:border-gray-700 mb-6">
      <!-- Current info -->
      <div v-if="store.pipelineStatus.current_modality" class="mb-4">
        <p class="text-sm text-gray-500 dark:text-gray-400">Current modality</p>
        <p class="font-semibold text-lg">{{ store.pipelineStatus.current_modality }}</p>
      </div>

      <div v-if="store.pipelineStatus.current_sample" class="mb-4">
        <p class="text-sm text-gray-500 dark:text-gray-400">Current sample</p>
        <p class="font-medium">
          {{ store.pipelineStatus.current_sample }}
          <span v-if="store.pipelineStatus.total_samples > 0" class="text-gray-400">
            ({{ store.pipelineStatus.current_sample_index }} / {{ store.pipelineStatus.total_samples }})
          </span>
        </p>
      </div>

      <!-- Message log -->
      <div class="bg-gray-50 dark:bg-gray-900 rounded p-3 text-sm font-mono min-h-[60px]">
        {{ store.pipelineStatus.message || 'Starting...' }}
      </div>
    </div>

    <!-- Alignment waiting -->
    <div
      v-if="store.pipelineStatus.state === 'alignment_waiting'"
      class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-400 dark:border-yellow-700 rounded-lg p-6 mb-6 text-center"
    >
      <p class="font-semibold mb-3">Manual alignment required</p>
      <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">
        The alignment tool will open in a new tab. Complete the alignment for each sample, then return here.
      </p>
      <button
        @click="openAlignmentTool"
        class="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-bold text-lg"
      >
        Open Alignment Tool
      </button>
    </div>

    <!-- Error state -->
    <div
      v-if="store.pipelineStatus.state === 'error'"
      class="bg-red-50 dark:bg-red-900/20 border border-red-400 dark:border-red-700 rounded-lg p-6 mb-6"
    >
      <h3 class="font-semibold text-red-700 dark:text-red-400 mb-2">Pipeline Error</h3>
      <p class="text-sm text-red-600 dark:text-red-400 font-mono mb-4">{{ store.pipelineStatus.error }}</p>
      <button
        @click="store.goToConfig()"
        class="px-4 py-2 bg-gray-200 dark:bg-gray-700 rounded hover:bg-gray-300 dark:hover:bg-gray-600 font-medium"
      >
        Back to Configuration
      </button>
    </div>
  </div>
</template>
