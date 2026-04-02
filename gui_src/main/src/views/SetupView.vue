<script setup lang="ts">
import { ref } from 'vue';
import { useMainStore } from '../store/main';
import ConfigUploader from '../components/ConfigUploader.vue';

const store = useMainStore();
const pathInput = ref(store.config.dataset_path || '');
const pathError = ref('');
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
  if (store.hasExistingConfig) {
    showExistingPrompt.value = true;
  } else {
    store.goToConfig();
  }
};

const loadExisting = async () => {
  const result = await store.loadExistingConfig();
  if (result.success) {
    showExistingPrompt.value = false;
    store.goToConfig();
  } else {
    // Config file is corrupted — ask the user what to do instead of silently overwriting
    showExistingPrompt.value = false;
    corruptedErrors.value = result.errors ?? ['Unknown error reading config file.'];
    showCorruptedPrompt.value = true;
  }
};

const skipExisting = () => {
  showExistingPrompt.value = false;
  store.goToConfig();
};

// User acknowledges corruption and wants to start fresh (overwrites the corrupted file)
const proceedFreshAfterCorruption = async () => {
  showCorruptedPrompt.value = false;
  // Immediately overwrite the corrupted file so the user has a clean slate
  await store.autoSave();
  store.goToConfig();
};

// User wants to go back and inspect / fix the corrupted file manually
const goBackFromCorruption = () => {
  showCorruptedPrompt.value = false;
};
</script>

<template>
  <div class="flex items-center justify-center min-h-screen">
    <div class="w-full max-w-xl p-8">
      <!-- Header -->
      <div class="text-center mb-10">
        <h1 class="text-4xl font-bold mb-2">FOCUS</h1>
        <p class="text-gray-500 dark:text-gray-400">Flexible Multiomics data preprocessing and alignment pipeline</p>
      </div>

      <!-- Dataset path input -->
      <div class="bg-white dark:bg-gray-800 rounded-lg shadow p-6 mb-6">
        <label class="block text-sm font-semibold mb-2">Dataset Path</label>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-3">
          Absolute path to the root directory containing your sample subdirectories.
        </p>
        <div class="flex gap-2">
          <input
            v-model="pathInput"
            type="text"
            placeholder="/path/to/dataset"
            class="flex-1 border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            @keyup.enter="onSetPath"
          />
          <button
            @click="onSetPath"
            class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-medium"
          >Continue</button>
        </div>
        <p v-if="pathError" class="text-red-500 text-sm mt-2">{{ pathError }}</p>
        <p v-if="store.samples.length > 0 && !showExistingPrompt" class="text-green-600 text-sm mt-2">
          {{ store.samples.length }} sample(s) found.
        </p>
      </div>

      <!-- Existing config prompt -->
      <div v-if="showExistingPrompt" class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-300 dark:border-yellow-700 rounded-lg p-4 mb-6">
        <p class="font-medium mb-3">Existing configuration found in this directory.</p>
        <div class="flex gap-3">
          <button @click="loadExisting" class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-medium">
            Load Existing Config
          </button>
          <button @click="skipExisting" class="px-4 py-2 bg-gray-200 dark:bg-gray-700 rounded hover:bg-gray-300 dark:hover:bg-gray-600 font-medium">
            Start Fresh
          </button>
        </div>
      </div>

      <!-- Corrupted config prompt -->
      <div v-if="showCorruptedPrompt" class="bg-red-50 dark:bg-red-900/20 border border-red-300 dark:border-red-700 rounded-lg p-4 mb-6">
        <p class="font-semibold text-red-700 dark:text-red-400 mb-2">Configuration file is corrupted and could not be loaded.</p>
        <ul class="text-sm text-red-600 dark:text-red-300 mb-4 list-disc list-inside space-y-1">
          <li v-for="(err, i) in corruptedErrors" :key="i">{{ err }}</li>
        </ul>
        <p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
          You can go back and inspect or repair the file manually, or proceed with a fresh configuration (this will overwrite the corrupted file).
        </p>
        <div class="flex gap-3">
          <button @click="proceedFreshAfterCorruption" class="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 font-medium">
            Proceed Fresh (Overwrite File)
          </button>
          <button @click="goBackFromCorruption" class="px-4 py-2 bg-gray-200 dark:bg-gray-700 rounded hover:bg-gray-300 dark:hover:bg-gray-600 font-medium">
            Go Back
          </button>
        </div>
      </div>

      <!-- Config upload -->
      <ConfigUploader />
    </div>
  </div>
</template>
