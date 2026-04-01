<script setup lang="ts">
import { computed } from 'vue';
import { useMainStore } from '../store/main';
import ProcessingSettingsForm from './ProcessingSettingsForm.vue';
import RegistrationSettingsForm from './RegistrationSettingsForm.vue';

const props = defineProps<{ index: number }>();
const store = useMainStore();

const modality = computed(() => store.config.modalities[props.index]!);

const compatibleRegistrationTypes = computed<string[]>(() => {
  if (!store.schema) return ['none'];
  const m = modality.value;
  const compat = store.schema.registration_compatibility;
  return store.schema.registration_types.filter(rt => {
    const allowed = compat[rt];
    return allowed === null || allowed === undefined || (m && allowed.includes(m.type));
  });
});

const onTypeChange = (newType: string) => {
  // Reset processing_settings to defaults for the new type
  const defaults: Record<string, unknown> = {};
  const specs = store.schema?.processing_params[newType];
  if (specs) {
    for (const [key, spec] of Object.entries(specs)) {
      defaults[key] = spec.default;
    }
  }
  store.updateModality(props.index, {
    type: newType,
    processing_settings: defaults,
    registration_type: 'none',
    registration_settings: {},
  });
};

const onRegistrationTypeChange = (newRegType: string) => {
  const defaults: Record<string, unknown> = {};
  const specs = store.schema?.registration_params[newRegType];
  if (specs) {
    for (const [key, spec] of Object.entries(specs)) {
      defaults[key] = spec.default;
    }
  }
  store.updateModality(props.index, {
    registration_type: newRegType,
    registration_settings: defaults,
  });
};

const confirmRemove = () => {
  if (confirm(`Remove modality "${modality.value.name || '(unnamed)'}"?`)) {
    store.removeModality(props.index);
  }
};
</script>

<template>
  <div class="bg-white dark:bg-gray-800 rounded-lg shadow p-5 border border-gray-200 dark:border-gray-700">
    <!-- Header -->
    <div class="flex items-center justify-between mb-4">
      <h3 class="font-semibold text-lg">Modality {{ index + 1 }}</h3>
      <button @click="confirmRemove" class="text-red-500 hover:text-red-700 text-sm font-medium">Remove</button>
    </div>

    <!-- Name -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="block text-sm font-medium mb-1">Name</label>
        <input
          :value="modality.name"
          @input="store.updateModality(index, { name: ($event.target as HTMLInputElement).value })"
          type="text"
          placeholder="e.g., Fluorescence"
          class="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600"
        />
      </div>

      <!-- Type -->
      <div>
        <label class="block text-sm font-medium mb-1">Type</label>
        <select
          :value="modality.type"
          @change="onTypeChange(($event.target as HTMLSelectElement).value)"
          class="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600"
        >
          <option v-for="t in store.schema?.modality_types" :key="t" :value="t">{{ t }}</option>
        </select>
      </div>
    </div>

    <!-- Processing settings -->
    <details class="mb-4" open>
      <summary class="text-sm font-semibold cursor-pointer mb-2">Processing Settings</summary>
      <ProcessingSettingsForm
        :modality-type="modality.type"
        :settings="modality.processing_settings"
        :modality-index="index"
      />
    </details>

    <!-- Registration type -->
    <div class="grid grid-cols-2 gap-4 mb-2">
      <div>
        <label class="block text-sm font-medium mb-1">Registration Type</label>
        <select
          :value="modality.registration_type"
          @change="onRegistrationTypeChange(($event.target as HTMLSelectElement).value)"
          class="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600"
        >
          <option v-for="rt in compatibleRegistrationTypes" :key="rt" :value="rt">{{ rt }}</option>
        </select>
      </div>
    </div>

    <!-- Registration settings -->
    <RegistrationSettingsForm
      v-if="modality.registration_type !== 'none'"
      :registration-type="modality.registration_type"
      :settings="modality.registration_settings"
      :modality-index="index"
    />
  </div>
</template>
