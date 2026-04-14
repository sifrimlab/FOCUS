<script setup lang="ts">
import { computed, ref } from 'vue';
import { useMainStore } from '../store/main';
import OutputSummary from '../components/OutputSummary.vue';
import type { OutputFiles } from '../api/types';

const store = useMainStore();

const confirmingCleanup = ref(false);
const cleanupDone = ref(false);

const totalPerSampleFiles = computed(() => {
  const files = store.pipelineStatus.output_files as OutputFiles;
  let count = 0;
  for (const key of Object.keys(files) as Array<keyof OutputFiles>) {
    if (key !== 'multimodal') {
      count += files[key]?.per_sample.length ?? 0;
    }
  }
  return count;
});

async function handleCleanup() {
  if (!confirmingCleanup.value) {
    confirmingCleanup.value = true;
    return;
  }
  await store.cleanupFiles();
  confirmingCleanup.value = false;
  cleanupDone.value = true;
}

function cancelCleanup() {
  confirmingCleanup.value = false;
}
</script>

<template>
  <div class="h-full flex flex-col items-center px-4 py-6 overflow-y-auto">
    <div class="w-full max-w-2xl flex flex-col gap-5">
      <!-- Success banner -->
      <div class="bg-green-50 dark:bg-green-900/20 border border-green-400 dark:border-green-700 rounded-lg p-8 text-center">
        <div class="text-5xl mb-4">&#10003;</div>
        <h1 class="text-2xl font-bold text-green-700 dark:text-green-400 mb-2">Pipeline Completed</h1>
        <p class="text-gray-600 dark:text-gray-300">All processing stages have finished successfully.</p>
      </div>

      <!-- Output files -->
      <OutputSummary :files="store.pipelineStatus.output_files" />

      <!-- Cleanup button -->
      <div v-if="totalPerSampleFiles > 0 || cleanupDone" class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 px-5 py-4">
        <p class="text-sm text-gray-600 dark:text-gray-400 mb-3">
          Per-sample intermediate files take extra disk space and are not needed once the merged outputs are available.
        </p>

        <div v-if="cleanupDone" class="text-sm text-green-600 dark:text-green-400 font-medium">
          Temporary files deleted.
        </div>

        <div v-else-if="confirmingCleanup" class="flex items-center gap-3">
          <span class="text-sm text-red-600 dark:text-red-400">
            Delete {{ totalPerSampleFiles }} per-sample file{{ totalPerSampleFiles !== 1 ? 's' : '' }}?
          </span>
          <button
            @click="handleCleanup"
            class="px-3 py-1.5 text-sm bg-red-600 text-white rounded hover:bg-red-700 font-medium"
          >
            Confirm
          </button>
          <button
            @click="cancelCleanup"
            class="px-3 py-1.5 text-sm bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200 rounded hover:bg-gray-300 dark:hover:bg-gray-500"
          >
            Cancel
          </button>
        </div>

        <button
          v-else
          @click="handleCleanup"
          class="px-4 py-2 text-sm bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-200 dark:hover:bg-gray-600"
        >
          Clean Temporary Files
        </button>
      </div>

      <!-- Start again -->
      <div class="text-center">
        <button
          @click="store.resetAll()"
          class="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-bold"
        >
          Start New Project
        </button>
      </div>
    </div>
  </div>
</template>
