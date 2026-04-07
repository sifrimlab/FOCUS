<script setup lang="ts">
import { computed, ref, nextTick } from 'vue';
import { useMainStore } from '../store/main';
import type { Modality } from '../api/types';
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
  if (confirm(`Remove modality "${modality.value.name}"?`)) {
    store.removeModality(props.index);
  }
};

// Inline name editing
const isEditingName = ref(false);
const editedName = ref('');
const nameInputRef = ref<HTMLInputElement | null>(null);

const startEditName = async () => {
  editedName.value = modality.value.name;
  isEditingName.value = true;
  await nextTick();
  nameInputRef.value?.focus();
  nameInputRef.value?.select();
};

const saveEditName = () => {
  if (!isEditingName.value) return;
  isEditingName.value = false;
  const name = editedName.value.trim();
  if (!name) return;
  const others = store.config.modalities
    .filter((_: Modality, i: number) => i !== props.index)
    .map((m: Modality) => m.name);
  if (others.includes(name)) return;
  if (name !== modality.value.name) {
    store.updateModality(props.index, { name });
  }
};

const cancelEditName = () => {
  isEditingName.value = false;
};
</script>

<template>
  <div class="bg-white dark:bg-gray-800 rounded-lg shadow p-5 border border-gray-200 dark:border-gray-700">
    <!-- Header: name + edit/remove buttons -->
    <div class="flex items-center justify-between mb-4">
      <!-- Modality name (display or inline edit) -->
      <div class="flex-1 min-w-0 mr-3">
        <template v-if="isEditingName">
          <input
            ref="nameInputRef"
            v-model="editedName"
            @keyup.enter="saveEditName"
            @keyup.escape="cancelEditName"
            @blur="saveEditName"
            type="text"
            class="text-base font-semibold bg-transparent border-b-2 border-blue-500 focus:outline-none text-gray-900 dark:text-gray-100 w-full"
          />
        </template>
        <template v-else>
          <h3 class="font-semibold text-base text-gray-900 dark:text-gray-100 truncate">{{ modality.name }}</h3>
        </template>
      </div>

      <!-- Action buttons -->
      <div class="flex gap-1.5 shrink-0">
        <!-- Edit name (blue) -->
        <button
          @click="startEditName"
          title="Edit name"
          class="w-7 h-7 flex items-center justify-center rounded bg-blue-500 hover:bg-blue-600 text-white transition-colors"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
          </svg>
        </button>
        <!-- Remove modality (red, minus symbol) -->
        <button
          @click="confirmRemove"
          title="Remove modality"
          class="w-7 h-7 flex items-center justify-center rounded bg-red-500 hover:bg-red-600 text-white transition-colors"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M20 12H4" />
          </svg>
        </button>
      </div>
    </div>

    <!-- Type -->
    <div class="mb-4">
      <label class="block text-sm font-medium mb-1">Type</label>
      <select
        :value="modality.type"
        @change="onTypeChange(($event.target as HTMLSelectElement).value)"
        class="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600"
      >
        <option v-for="t in store.schema?.modality_types" :key="t" :value="t">{{ t }}</option>
      </select>
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

    <!-- Registration type (only when perform_registration is enabled) -->
    <template v-if="store.config.perform_registration">
      <div class="mb-2">
        <label class="block text-sm font-medium mb-1">Registration Type</label>
        <select
          :value="modality.registration_type"
          @change="onRegistrationTypeChange(($event.target as HTMLSelectElement).value)"
          class="w-full border rounded px-3 py-2 dark:bg-gray-700 dark:border-gray-600"
        >
          <option v-for="rt in compatibleRegistrationTypes" :key="rt" :value="rt">{{ rt }}</option>
        </select>
      </div>

      <RegistrationSettingsForm
        v-if="modality.registration_type !== 'none'"
        :registration-type="modality.registration_type"
        :settings="modality.registration_settings"
        :modality-index="index"
      />
    </template>
  </div>
</template>