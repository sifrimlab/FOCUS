<script setup lang="ts">
import { computed } from 'vue';
import { useMainStore } from '../store/main';

const store = useMainStore();

const hasInfo = computed(() => !!store.sampleInfo);

const progress = computed(() => {
  if (!store.sampleInfo || store.sampleInfo.total_samples_count === 0) return 0;
  return (store.sampleInfo.sample_index / store.sampleInfo.total_samples_count) * 100;
});

const message = computed(() => {
  if (store.loadingMessage) {
    return store.loadingMessage;
  }
  if (!store.sampleInfo) {
    return "Obtaining dataset informations...";
  }
  return `Loading sample ${store.sampleInfo.sample_index} of ${store.sampleInfo.total_samples_count}`;
});

const subMessage = computed(() => {
    if (store.loadingMessage && store.sampleInfo) {
        return `Sample ${store.sampleInfo.sample_index} of ${store.sampleInfo.total_samples_count}`;
    }
    return null;
});
</script>

<template>
  <div class="fixed inset-0 z-40 flex flex-col items-center justify-center bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white">
    <div class="animate-spin rounded-full h-16 w-16 border-t-4 border-b-4 border-blue-500 mb-6"></div>

    <h2 class="text-xl font-semibold mb-2">{{ message }}</h2>
    <p v-if="subMessage" class="text-gray-500 dark:text-gray-400 mb-4 font-mono" style="font-feature-settings: 'zero'">{{ subMessage }}</p>

    <div v-if="hasInfo" class="w-80 bg-gray-200 dark:bg-gray-700 rounded-full h-4 overflow-hidden">
      <div
        class="bg-blue-500 h-full rounded-full transition-all duration-500 ease-out"
        :style="{ width: `${progress}%` }"
      ></div>
    </div>
  </div>
</template>
