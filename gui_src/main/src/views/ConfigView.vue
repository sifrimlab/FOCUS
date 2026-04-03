<script setup lang="ts">
import { useMainStore } from '../store/main';
import ModalityCard from '../components/ModalityCard.vue';
import ConfigUploader from '../components/ConfigUploader.vue';

const store = useMainStore();

const confirmReset = () => {
  if (confirm('Reset all configuration? This cannot be undone.')) {
    store.resetAll();
  }
};
</script>

<template>
  <div class="max-w-4xl mx-auto px-4 py-8">
    <!-- Header -->
    <div class="flex items-center justify-between mb-8">
      <div>
        <h1 class="text-2xl font-bold">FOCUS Configuration</h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Dataset: <code class="bg-gray-100 dark:bg-gray-800 px-1 rounded">{{ store.config.dataset_path }}</code>
          &mdash; {{ store.samples.length }} sample(s) found
        </p>
      </div>
    </div>

    <!-- Top-level settings -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow p-5 mb-6 border border-gray-200 dark:border-gray-700">
      <h2 class="font-semibold text-lg mb-4">Pipeline Settings</h2>

      <div class="grid grid-cols-2 gap-4 mb-4">
        <!-- Reference modality -->
        <div>
          <label class="block text-sm font-medium mb-1">Reference Modality</label>
          <select
            v-model="store.config.reference_modality"
            @change="store.triggerAutoSave()"
            class="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600"
          >
            <option value="" disabled>Select reference...</option>
            <option v-for="name in store.modalityNames" :key="name" :value="name">{{ name }}</option>
          </select>
        </div>

        <!-- HuggingFace token (shown only when needed) -->
        <div v-if="store.needsHuggingfaceToken">
          <label class="block text-sm font-medium mb-1">HuggingFace Token</label>
          <input
            v-model="store.config.huggingface_token"
            @input="store.triggerAutoSave()"
            type="password"
            placeholder="hf_..."
            class="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600"
          />
        </div>
      </div>

      <div class="flex gap-8">
        <!-- Perform alignment -->
        <div class="flex items-center gap-3">
          <label class="text-sm font-medium">Perform Alignment</label>
          <label class="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              v-model="store.config.perform_alignment"
              @change="() => { if (!store.config.perform_alignment) { store.config.perform_registration = false; store.config.alignment_force_recomputing = false; } store.triggerAutoSave(); }"
              class="sr-only peer"
            />
            <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-blue-600 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
          </label>
        </div>

        <!-- Force alignment recomputing -->
        <div class="flex items-center gap-3" :class="{ 'opacity-40': !store.config.perform_alignment }">
          <label class="text-sm font-medium">Force Alignment Recomputing</label>
          <label class="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              v-model="store.config.alignment_force_recomputing"
              :disabled="!store.config.perform_alignment"
              @change="store.triggerAutoSave()"
              class="sr-only peer"
            />
            <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-orange-500 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
          </label>
        </div>

        <!-- Perform registration -->
        <div class="flex items-center gap-3">
          <label class="text-sm font-medium" :class="{ 'opacity-40': !store.config.perform_alignment }">
            Perform Registration
          </label>
          <label class="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              v-model="store.config.perform_registration"
              :disabled="!store.config.perform_alignment"
              @change="store.triggerAutoSave()"
              class="sr-only peer"
            />
            <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-blue-600 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full" :class="{ 'opacity-40': !store.config.perform_alignment }"></div>
          </label>
        </div>
      </div>
    </div>

    <!-- Modalities -->
    <div class="mb-6">
      <h2 class="font-semibold text-lg mb-4">Modalities</h2>
      <div class="space-y-4">
        <ModalityCard v-for="(_, i) in store.config.modalities" :key="i" :index="i" />
      </div>
      <button
        @click="store.addModality()"
        class="mt-4 w-full py-3 border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-gray-500 hover:border-blue-500 hover:text-blue-500 font-medium transition-colors"
      >
        + Add Modality
      </button>
    </div>

    <!-- Config file upload -->
    <div class="mb-6">
      <ConfigUploader />
    </div>

    <!-- Validation errors -->
    <div v-if="store.validationErrors.length > 0" class="bg-red-50 dark:bg-red-900/20 border border-red-300 dark:border-red-700 rounded-lg p-4 mb-6">
      <h3 class="font-semibold text-red-700 dark:text-red-400 mb-2">Validation Errors</h3>
      <ul class="list-disc pl-5 text-sm text-red-600 dark:text-red-400">
        <li v-for="(err, i) in store.validationErrors" :key="i">{{ err }}</li>
      </ul>
    </div>

    <!-- Action bar -->
    <div class="sticky bottom-0 bg-gray-50 dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 py-4 flex items-center justify-between">
      <div class="flex gap-3">
        <button @click="store.goToSetup()" class="px-4 py-2 bg-gray-200 dark:bg-gray-700 rounded hover:bg-gray-300 dark:hover:bg-gray-600 font-medium">
          Back
        </button>
        <button @click="confirmReset" class="px-4 py-2 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400 rounded hover:bg-red-200 font-medium">
          Reset
        </button>
      </div>

      <button
        @click="store.startPipeline()"
        :disabled="store.isLoading"
        class="px-6 py-2 bg-green-600 text-white rounded hover:bg-green-700 font-bold disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {{ store.isLoading ? 'Validating...' : 'Start Processing' }}
      </button>
    </div>
  </div>
</template>
