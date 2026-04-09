<script setup lang="ts">
import { ref, nextTick } from 'vue';
import { useMainStore } from '../store/main';
import type { SpatialAnnotations } from '../api/types';
import ModalityCard from '../components/ModalityCard.vue';
import ConfigUploader from '../components/ConfigUploader.vue';
import { useDialog } from '../composables/useDialog';

const store = useMainStore();
const { showConfirm } = useDialog();

const confirmReset = async () => {
  const ok = await showConfirm({
    message: 'Reset all configuration? This cannot be undone.',
    confirmLabel: 'Reset',
    variant: 'danger',
  });
  if (ok) store.resetAll();
};

// Add modality: name-entry inline form
const showNameEntry = ref(false);
const newModalityName = ref('');
const nameEntryError = ref('');
const nameInputRef = ref<HTMLInputElement | null>(null);

const openAddModality = async () => {
  newModalityName.value = '';
  nameEntryError.value = '';
  showNameEntry.value = true;
  await nextTick();
  nameInputRef.value?.focus();
};

const confirmAddModality = () => {
  const name = newModalityName.value.trim();
  if (!name) {
    nameEntryError.value = 'Please enter a name.';
    return;
  }
  if (store.modalityNames.includes(name)) {
    nameEntryError.value = 'A modality with this name already exists.';
    return;
  }
  store.addModality(name);
  showNameEntry.value = false;
};

const cancelAddModality = () => {
  showNameEntry.value = false;
};

const toggleAnnotations = () => {
  if (store.config.spatial_annotations === null) {
    store.config.spatial_annotations = {
      modality_name: store.modalityNames[0] ?? '',
      file_type: store.schema?.annotation_file_types[0] ?? 'geojson',
    } as SpatialAnnotations;
  } else {
    store.config.spatial_annotations = null;
  }
  store.triggerAutoSave();
};

const confirmRemoveAll = async () => {
  const ok = await showConfirm({
    message: 'Remove all modalities? This cannot be undone.',
    confirmLabel: 'Remove All',
    variant: 'danger',
  });
  if (ok) store.removeAllModalities();
};
</script>

<template>
  <div class="max-w-4xl mx-auto px-4 py-8">
    <!-- Header -->
    <div class="mb-8">
      <h1 class="text-2xl font-bold">Configuration Builder</h1>
      <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
        Dataset: <code class="bg-gray-100 dark:bg-gray-800 px-1 rounded">{{ store.config.dataset_path }}</code>
        &mdash; {{ store.samples.length }} sample(s) found
      </p>
    </div>

    <!-- Pipeline Settings card -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow border border-gray-200 dark:border-gray-700 mb-6">
      <!-- Card header -->
      <div class="px-5 py-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg">
        <span class="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">Pipeline Settings</span>
      </div>

      <div class="px-5 py-5 space-y-4">
        <!-- Reference modality — inline -->
        <div class="flex items-center justify-between gap-4">
          <label class="text-sm font-medium shrink-0">Reference Modality</label>
          <select
            v-model="store.config.reference_modality"
            @change="store.triggerAutoSave()"
            class="border rounded px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600 w-56"
          >
            <option value="" disabled>Select reference...</option>
            <option v-for="name in store.modalityNames" :key="name" :value="name">{{ name }}</option>
          </select>
        </div>

        <!-- HuggingFace token (shown only when needed) — inline -->
        <div v-if="store.needsHuggingfaceToken" class="flex items-center justify-between gap-4">
          <label class="text-sm font-medium shrink-0">HuggingFace Token</label>
          <input
            v-model="store.config.huggingface_token"
            @input="store.triggerAutoSave()"
            type="password"
            placeholder="hf_..."
            class="border rounded px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600 w-56"
          />
        </div>

        <!-- Toggles -->
        <div class="flex flex-wrap gap-x-8 gap-y-3 pt-1">
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
          <div class="flex items-center gap-3" :class="{ 'opacity-40': !store.config.perform_alignment }">
            <label class="text-sm font-medium">Perform Registration</label>
            <label class="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                v-model="store.config.perform_registration"
                :disabled="!store.config.perform_alignment"
                @change="store.triggerAutoSave()"
                class="sr-only peer"
              />
              <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-blue-600 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
            </label>
          </div>
        </div>
      </div>
    </div>

    <!-- Spatial Annotations card -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow border border-gray-200 dark:border-gray-700 mb-6">
      <div class="px-5 py-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg">
        <span class="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">Spatial Annotations</span>
      </div>

      <div class="px-5 py-5 space-y-4">
        <!-- Enable / disable toggle -->
        <div class="flex items-center gap-3">
          <label class="text-sm font-medium">Load Spatial Annotations</label>
          <label class="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              :checked="store.config.spatial_annotations !== null"
              @change="toggleAnnotations()"
              class="sr-only peer"
            />
            <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-blue-600 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
          </label>
        </div>

        <!-- Settings (shown only when enabled) -->
        <template v-if="store.config.spatial_annotations !== null">
          <!-- Annotation modality -->
          <div class="flex items-center justify-between gap-4">
            <label class="text-sm font-medium shrink-0">Annotation Modality</label>
            <select
              v-model="store.config.spatial_annotations.modality_name"
              @change="store.triggerAutoSave()"
              class="border rounded px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600 w-56"
            >
              <option value="" disabled>Select modality...</option>
              <option v-for="name in store.modalityNames" :key="name" :value="name">{{ name }}</option>
            </select>
          </div>

          <!-- File type -->
          <div class="flex items-center justify-between gap-4">
            <label class="text-sm font-medium shrink-0">Annotation File Type</label>
            <select
              v-model="store.config.spatial_annotations.file_type"
              @change="store.triggerAutoSave()"
              class="border rounded px-3 py-2 text-sm dark:bg-gray-700 dark:border-gray-600 w-56"
            >
              <option v-for="ft in store.schema?.annotation_file_types ?? ['geojson']" :key="ft" :value="ft">
                {{ ft }}
              </option>
            </select>
          </div>

          <p class="text-xs text-gray-500 dark:text-gray-400">
            FOCUS expects one annotation file per sample in
            <code class="bg-gray-100 dark:bg-gray-700 px-1 rounded">{sample_id}/{{ store.config.spatial_annotations.modality_name }}/</code>.
          </p>
        </template>
      </div>
    </div>

    <!-- Modalities outer card -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow border border-gray-200 dark:border-gray-700 mb-6">
      <!-- Card header with action buttons -->
      <div class="flex items-center justify-between px-5 py-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg">
        <span class="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">Modalities</span>
        <div class="flex gap-2">
          <!-- Add modality (green, +) -->
          <button
            @click="openAddModality"
            title="Add modality"
            class="w-7 h-7 flex items-center justify-center rounded bg-emerald-500 hover:bg-emerald-600 text-white transition-colors"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" />
            </svg>
          </button>
          <!-- Remove all (red, trash) -->
          <button
            v-if="store.config.modalities.length > 0"
            @click="confirmRemoveAll"
            title="Remove all modalities"
            class="w-7 h-7 flex items-center justify-center rounded bg-red-500 hover:bg-red-600 text-white transition-colors"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Inline name-entry form -->
      <div
        v-if="showNameEntry"
        class="px-5 py-3 bg-emerald-50 dark:bg-emerald-900/10 border-b border-emerald-200 dark:border-emerald-800"
      >
        <p class="text-xs font-semibold uppercase tracking-widest text-emerald-600 dark:text-emerald-400 mb-2">New modality name</p>
        <div class="flex gap-2">
          <input
            ref="nameInputRef"
            v-model="newModalityName"
            @keyup.enter="confirmAddModality"
            @keyup.escape="cancelAddModality"
            type="text"
            placeholder="e.g., Fluorescence"
            class="flex-1 border border-gray-300 dark:border-gray-600 rounded px-3 py-1.5 text-sm bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
          />
          <button
            @click="confirmAddModality"
            class="px-4 py-1.5 bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-semibold rounded transition-colors"
          >
            Add
          </button>
          <button
            @click="cancelAddModality"
            class="px-3 py-1.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-sm font-medium rounded transition-colors"
          >
            Cancel
          </button>
        </div>
        <p v-if="nameEntryError" class="text-red-500 text-xs mt-1.5">{{ nameEntryError }}</p>
      </div>

      <!-- Modality cards -->
      <div class="p-4 space-y-4">
        <ModalityCard v-for="(_, i) in store.config.modalities" :key="i" :index="i" />

        <!-- Empty state -->
        <div
          v-if="store.config.modalities.length === 0 && !showNameEntry"
          class="text-center py-8 text-gray-400 dark:text-gray-500 text-sm italic border-2 border-dashed border-gray-200 dark:border-gray-700 rounded-lg"
        >
          No modalities yet — click + to add one.
        </div>
      </div>
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