<script setup lang="ts">
import { ref } from 'vue';
import { useMainStore } from '../store/main';

const store = useMainStore();
const pathInput = ref(store.config.dataset_path || '');
const pathError = ref('');
const showSamplesPrompt = ref(false);
const showExistingPrompt = ref(false);
const showCorruptedPrompt = ref(false);
const corruptedErrors = ref<string[]>([]);

const onSetPath = async () => {
  pathError.value = '';
  if (!pathInput.value.trim()) {
    pathError.value = 'Please enter a dataset path.';
    return;
  }
  await store.setDatasetPath(pathInput.value.trim());
  if (store.samples.length === 0 && !store.hasExistingConfig) {
    pathError.value = 'No sample directories found at this path. Please check the path.';
    return;
  }
  showSamplesPrompt.value = true;
};

const confirmSamples = () => {
  showSamplesPrompt.value = false;
  if (store.hasExistingConfig) {
    showExistingPrompt.value = true;
  } else {
    store.goToConfig();
  }
};

const backFromSamples = () => {
  showSamplesPrompt.value = false;
};

const loadExisting = async () => {
  const result = await store.loadExistingConfig();
  if (result.success) {
    showExistingPrompt.value = false;
    store.goToConfig();
  } else {
    showExistingPrompt.value = false;
    corruptedErrors.value = result.errors ?? ['Unknown error reading config file.'];
    showCorruptedPrompt.value = true;
  }
};

const skipExisting = () => {
  showExistingPrompt.value = false;
  store.goToConfig();
};

const proceedFreshAfterCorruption = async () => {
  showCorruptedPrompt.value = false;
  await store.autoSave();
  store.goToConfig();
};

const goBackFromCorruption = () => {
  showCorruptedPrompt.value = false;
};
</script>

<template>
  <div class="h-full flex flex-col items-center justify-center px-4">
    <div class="w-full max-w-lg">

      <!-- Header -->
      <div class="text-center mb-12">
        <h1 class="text-5xl font-bold tracking-tight mb-3">FOCUS</h1>
        <p class="text-gray-500 dark:text-gray-400 text-sm">
          Flexible Multiomics data preprocessing and alignment pipeline
        </p>
      </div>

      <!-- Path input card -->
      <div
        v-if="!showSamplesPrompt && !showExistingPrompt && !showCorruptedPrompt"
        class="bg-white dark:bg-gray-800 rounded-2xl shadow-lg border border-gray-200 dark:border-gray-700 p-8"
      >
        <h2 class="text-base font-semibold text-gray-800 dark:text-gray-100 mb-1">
          Dataset path
        </h2>
        <p class="text-xs text-gray-400 dark:text-gray-500 mb-5">
          Absolute path to the root directory containing your sample subdirectories.
        </p>

        <!-- Path input with folder icon -->
        <div
          class="flex items-center gap-2 rounded-xl border px-4 py-3 transition-colors focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500"
          :class="pathError ? 'border-red-400 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'"
        >
          <!-- Folder icon -->
          <svg class="w-4 h-4 text-gray-400 dark:text-gray-500 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round"
              d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
          </svg>

          <!-- Slash separator hinting at absolute path -->
          <span class="text-gray-300 dark:text-gray-600 font-mono text-sm select-none">/</span>

          <input
            v-model="pathInput"
            type="text"
            placeholder="path/to/dataset"
            class="flex-1 bg-transparent text-sm font-mono text-gray-800 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-600 focus:outline-none"
            @keyup.enter="onSetPath"
          />
        </div>

        <p v-if="pathError" class="text-red-500 text-xs mt-2">{{ pathError }}</p>

        <button
          @click="onSetPath"
          class="mt-5 w-full py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-xl transition-colors"
        >
          Continue
        </button>
      </div>

      <!-- Samples confirmation -->
      <div
        v-if="showSamplesPrompt"
        class="bg-white dark:bg-gray-800 rounded-2xl shadow-lg border border-gray-200 dark:border-gray-700 p-8"
      >
        <div class="flex items-center gap-3 mb-1">
          <span class="flex items-center justify-center w-7 h-7 rounded-full bg-green-100 dark:bg-green-900/40 shrink-0">
            <svg class="w-4 h-4 text-green-600 dark:text-green-400" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </span>
          <h2 class="font-semibold text-gray-800 dark:text-gray-100">
            <span class="text-blue-600 dark:text-blue-400">{{ store.samples.length }}</span>
            sample{{ store.samples.length !== 1 ? 's' : '' }} found
          </h2>
        </div>
        <p class="text-xs text-gray-400 dark:text-gray-500 mb-4 ml-10">
          Verify the list matches your dataset before continuing.
        </p>

        <!-- Sample list -->
        <div
          v-if="store.samples.length > 0"
          class="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden overflow-y-auto mb-5"
          style="max-height: min(220px, 30vh)"
        >
          <div
            v-for="(sample, idx) in store.samples"
            :key="sample"
            class="flex items-center gap-3 px-4 py-2 text-sm"
            :class="idx % 2 === 0 ? 'bg-gray-50 dark:bg-gray-900/50' : 'bg-white dark:bg-gray-800'"
          >
            <span class="text-xs font-mono text-gray-400 dark:text-gray-500 w-7 shrink-0 text-right select-none">
              {{ idx + 1 }}
            </span>
            <span class="font-mono text-gray-700 dark:text-gray-200 truncate">{{ sample }}</span>
          </div>
        </div>
        <p v-else class="text-sm text-yellow-600 dark:text-yellow-400 italic mb-5">
          No sample directories found. An existing configuration file was detected.
        </p>

        <div class="flex gap-3">
          <button
            @click="confirmSamples"
            class="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-xl transition-colors"
          >
            Confirm &amp; Continue
          </button>
          <button
            @click="backFromSamples"
            class="px-5 py-2.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-sm font-medium rounded-xl transition-colors"
          >
            Back
          </button>
        </div>
      </div>

      <!-- Existing config prompt -->
      <div
        v-if="showExistingPrompt"
        class="bg-white dark:bg-gray-800 rounded-2xl shadow-lg border border-yellow-300 dark:border-yellow-700 p-8"
      >
        <div class="flex items-start gap-3 mb-5">
          <svg class="w-5 h-5 text-yellow-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          </svg>
          <div>
            <p class="font-semibold text-gray-800 dark:text-gray-100 mb-1">Existing configuration found</p>
            <p class="text-xs text-gray-500 dark:text-gray-400">
              A <code class="bg-gray-100 dark:bg-gray-700 px-1 rounded">focus_config.json</code> file already exists in this directory.
              Would you like to load it or start fresh?
            </p>
          </div>
        </div>
        <div class="flex gap-3">
          <button
            @click="loadExisting"
            class="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-xl transition-colors"
          >
            Load Existing Config
          </button>
          <button
            @click="skipExisting"
            class="px-5 py-2.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-sm font-medium rounded-xl transition-colors"
          >
            Start Fresh
          </button>
        </div>
      </div>

      <!-- Corrupted config prompt -->
      <div
        v-if="showCorruptedPrompt"
        class="bg-white dark:bg-gray-800 rounded-2xl shadow-lg border border-red-300 dark:border-red-700 p-8"
      >
        <p class="font-semibold text-red-700 dark:text-red-400 mb-2">Configuration file is corrupted</p>
        <ul class="text-xs text-red-600 dark:text-red-300 mb-4 list-disc list-inside space-y-1">
          <li v-for="(err, i) in corruptedErrors" :key="i">{{ err }}</li>
        </ul>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-5">
          You can go back and repair the file manually, or proceed with a fresh configuration (the corrupted file will be overwritten).
        </p>
        <div class="flex gap-3">
          <button
            @click="proceedFreshAfterCorruption"
            class="flex-1 py-2.5 bg-red-600 hover:bg-red-700 text-white text-sm font-semibold rounded-xl transition-colors"
          >
            Proceed Fresh (Overwrite)
          </button>
          <button
            @click="goBackFromCorruption"
            class="px-5 py-2.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-sm font-medium rounded-xl transition-colors"
          >
            Go Back
          </button>
        </div>
      </div>

    </div>
  </div>
</template>