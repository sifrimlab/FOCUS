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
    alignment_strategy: 'manual',
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

// Alignment settings visibility: only for non-reference spot modalities when alignment is enabled
const showAlignmentSettings = computed(() => {
  if (!store.schema || !store.config.perform_alignment) return false;
  if (modality.value.name === store.config.reference_modality) return false;
  const compatible = store.schema.alignment_strategy_compatibility['pre_aligned'];
  return compatible !== null && compatible !== undefined && compatible.includes(modality.value.type);
});

const onAlignmentStrategyChange = (newStrategy: string) => {
  store.updateModality(props.index, { alignment_strategy: newStrategy });
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
  <div class="bg-white dark:bg-gray-800 rounded-lg shadow border border-gray-200 dark:border-gray-700">
    <!-- Card header -->
    <div class="flex items-center justify-between px-5 py-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg">
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
            class="text-xs font-semibold uppercase tracking-widest bg-transparent border-b border-blue-500 focus:outline-none text-gray-600 dark:text-gray-300 w-full"
          />
        </template>
        <template v-else>
          <span class="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 truncate">{{ modality.name }}</span>
        </template>
      </div>

      <!-- Action buttons -->
      <div class="flex gap-1.5 shrink-0">
        <!-- Edit name (blue) -->
        <button
          @click="startEditName"
          title="Edit name"
          class="w-6 h-6 flex items-center justify-center rounded bg-blue-500 hover:bg-blue-600 text-white transition-colors"
        >
          <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
          </svg>
        </button>
        <!-- Remove modality (red, minus) -->
        <button
          @click="confirmRemove"
          title="Remove modality"
          class="w-6 h-6 flex items-center justify-center rounded bg-red-500 hover:bg-red-600 text-white transition-colors"
        >
          <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M20 12H4" />
          </svg>
        </button>
      </div>
    </div>

    <!-- Card body -->
    <div class="px-5 py-4 space-y-4">
      <!-- Type — inline -->
      <div class="flex items-center justify-between gap-4">
        <label class="text-sm font-medium shrink-0">Type</label>
        <select
          :value="modality.type"
          @change="onTypeChange(($event.target as HTMLSelectElement).value)"
          class="border rounded px-2 py-1.5 text-sm dark:bg-gray-700 dark:border-gray-600 w-48"
        >
          <option v-for="t in store.schema?.modality_types" :key="t" :value="t">{{ t }}</option>
        </select>
      </div>

      <!-- Processing settings -->
      <details open>
        <summary class="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 cursor-pointer select-none">
          Processing Settings
        </summary>
        <div class="mt-3">
          <ProcessingSettingsForm
            :modality-type="modality.type"
            :settings="modality.processing_settings"
            :modality-index="index"
          />
        </div>
      </details>

      <!-- Alignment settings (only for non-reference spot modalities when alignment is enabled) -->
      <template v-if="showAlignmentSettings">
        <!-- Strategy dropdown — inline -->
        <div class="flex items-center justify-between gap-4">
          <label class="text-sm font-medium shrink-0">Alignment Strategy</label>
          <select
            :value="modality.alignment_strategy"
            @change="onAlignmentStrategyChange(($event.target as HTMLSelectElement).value)"
            class="border rounded px-2 py-1.5 text-sm dark:bg-gray-700 dark:border-gray-600 w-48"
          >
            <option value="manual">Manual Alignment</option>
            <option value="pre_aligned">Pre-Aligned</option>
          </select>
        </div>

        <!-- Warning for pre_aligned -->
        <div
          v-if="modality.alignment_strategy === 'pre_aligned'"
          class="flex items-start gap-2 px-3 py-2.5 bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-700 rounded-lg text-sm text-orange-700 dark:text-orange-300"
        >
          <svg class="w-4 h-4 shrink-0 mt-0.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          </svg>
          <span>The spatial coordinates of this modality are assumed to be already expressed in the reference modality's coordinate system. No interactive alignment will be performed.</span>
        </div>
      </template>

      <!-- Registration (only when perform_registration is enabled) -->
      <template v-if="store.config.perform_registration">
        <!-- Registration type — inline -->
        <div class="flex items-center justify-between gap-4">
          <label class="text-sm font-medium shrink-0">Registration Type</label>
          <select
            :value="modality.registration_type"
            @change="onRegistrationTypeChange(($event.target as HTMLSelectElement).value)"
            class="border rounded px-2 py-1.5 text-sm dark:bg-gray-700 dark:border-gray-600 w-48"
          >
            <option v-for="rt in compatibleRegistrationTypes" :key="rt" :value="rt">{{ rt }}</option>
          </select>
        </div>

        <details v-if="modality.registration_type !== 'none'" open>
          <summary class="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 cursor-pointer select-none">
            Registration Settings
          </summary>
          <div class="mt-3">
            <RegistrationSettingsForm
              :registration-type="modality.registration_type"
              :settings="modality.registration_settings"
              :modality-index="index"
            />
          </div>
        </details>
      </template>
    </div>
  </div>
</template>