<script setup lang="ts">
import { computed } from 'vue';
import { useMainStore } from '../store/main';
import type { ParamSpec } from '../api/types';

const props = defineProps<{
  modalityType: string;
  settings: Record<string, unknown>;
  modalityIndex: number;
}>();

const store = useMainStore();

const paramSpecs = computed<Record<string, ParamSpec>>(() => {
  return store.schema?.processing_params[props.modalityType] || {};
});

const updateSetting = (key: string, value: unknown) => {
  const updated = { ...props.settings, [key]: value };
  store.updateModality(props.modalityIndex, { processing_settings: updated });
};

const parseNumeric = (spec: ParamSpec, raw: string): unknown => {
  if (raw === '' && spec.nullable) return null;
  if (spec.type === 'int') return parseInt(raw, 10) || 0;
  if (spec.type === 'float') return parseFloat(raw) || 0;
  return raw;
};

const formatLabel = (key: string): string => {
  return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
};
</script>

<template>
  <div class="space-y-3">
    <div v-for="(spec, key) in paramSpecs" :key="key" class="flex items-center justify-between gap-4">
      <label class="text-sm flex-shrink-0 min-w-[180px]">{{ formatLabel(key as string) }}</label>

      <!-- Bool toggle -->
      <label v-if="spec.type === 'bool'" class="relative inline-flex items-center cursor-pointer">
        <input
          type="checkbox"
          :checked="Boolean(settings[key as string] ?? spec.default)"
          @change="updateSetting(key as string, ($event.target as HTMLInputElement).checked)"
          class="sr-only peer"
        />
        <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-blue-600 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
      </label>

      <!-- Enum dropdown -->
      <select
        v-else-if="spec.type === 'enum'"
        :value="settings[key as string] ?? spec.default"
        @change="updateSetting(key as string, ($event.target as HTMLSelectElement).value)"
        class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600 w-40"
      >
        <option v-for="opt in spec.options" :key="opt" :value="opt">{{ opt }}</option>
      </select>

      <!-- Int input -->
      <input
        v-else-if="spec.type === 'int'"
        type="number"
        step="1"
        :value="settings[key as string] ?? spec.default ?? ''"
        :placeholder="spec.nullable ? 'optional' : String(spec.default)"
        @change="updateSetting(key as string, parseNumeric(spec, ($event.target as HTMLInputElement).value))"
        class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600 w-40"
      />

      <!-- Float input -->
      <input
        v-else-if="spec.type === 'float'"
        type="number"
        step="0.01"
        :value="settings[key as string] ?? spec.default ?? ''"
        :placeholder="spec.nullable ? 'optional' : String(spec.default)"
        @change="updateSetting(key as string, parseNumeric(spec, ($event.target as HTMLInputElement).value))"
        class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600 w-40"
      />

      <!-- String input -->
      <input
        v-else
        type="text"
        :value="settings[key as string] ?? spec.default ?? ''"
        :placeholder="spec.nullable ? 'optional' : ''"
        @change="updateSetting(key as string, ($event.target as HTMLInputElement).value || (spec.nullable ? null : ''))"
        class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600 w-40"
      />
    </div>
  </div>
</template>
