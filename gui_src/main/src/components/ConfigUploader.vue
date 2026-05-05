<script setup lang="ts">
import { ref } from 'vue';
import { useMainStore } from '../store/main';

const store = useMainStore();
const isDragging = ref(false);
const uploadError = ref('');
const uploadSuccess = ref(false);

const handleDrop = async (e: DragEvent) => {
  isDragging.value = false;
  uploadError.value = '';
  uploadSuccess.value = false;
  const file = e.dataTransfer?.files[0];
  if (!file || !file.name.endsWith('.json')) {
    uploadError.value = 'Please drop a .json file.';
    return;
  }
  await readAndLoad(file);
};

const handleFileInput = async (e: Event) => {
  uploadError.value = '';
  uploadSuccess.value = false;
  const target = e.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) return;
  await readAndLoad(file);
  target.value = '';
};

const readAndLoad = async (file: File) => {
  const text = await file.text();
  const ok = await store.loadConfigFromFile(text);
  if (ok) {
    uploadSuccess.value = true;
    store.goToConfig();
  } else {
    uploadError.value = store.validationErrors.join('; ');
  }
};
</script>

<template>
  <div class="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
    <label class="block text-sm font-semibold mb-2">Or Load a Config File</label>
    <div
      class="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors"
      :class="isDragging ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-gray-300 dark:border-gray-600 hover:border-gray-400'"
      @dragover.prevent="isDragging = true"
      @dragleave.prevent="isDragging = false"
      @drop.prevent="handleDrop"
      @click="($refs.fileInput as HTMLInputElement)?.click()"
    >
      <p class="text-gray-500 dark:text-gray-400">
        Drag & drop a <strong>.json</strong> config file here, or click to browse
      </p>
      <input ref="fileInput" type="file" accept=".json" class="hidden" @change="handleFileInput" />
    </div>
    <p v-if="uploadError" class="text-red-500 text-sm mt-2">{{ uploadError }}</p>
    <p v-if="uploadSuccess" class="text-green-600 text-sm mt-2">Configuration loaded successfully.</p>
  </div>
</template>
