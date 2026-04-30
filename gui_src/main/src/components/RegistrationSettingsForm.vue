<script setup lang="ts">
import { computed } from 'vue';
import { useMainStore } from '../store/main';
import type { ParamSpec } from '../api/types';

const props = defineProps<{
  registrationType: string;
  settings: Record<string, unknown>;
  modalityIndex: number;
}>();

const store = useMainStore();

const paramSpecs = computed<Record<string, ParamSpec>>(() => {
  return store.schema?.registration_params[props.registrationType] || {};
});

// Order: text inputs (string/int/float) → enum dropdowns → bool toggles; then alphabetical within each group
const TYPE_ORDER: Record<string, number> = { string: 0, int: 0, float: 0, enum: 1, bool: 2 };

const sortedEntries = computed<[string, ParamSpec][]>(() => {
  return (Object.entries(paramSpecs.value) as [string, ParamSpec][]).sort(
    ([keyA, specA], [keyB, specB]) => {
      const diff = (TYPE_ORDER[specA.type] ?? 0) - (TYPE_ORDER[specB.type] ?? 0);
      return diff !== 0 ? diff : keyA.localeCompare(keyB);
    }
  );
});

const updateSetting = (key: string, value: unknown) => {
  const updated = { ...props.settings, [key]: value };
  store.updateModality(props.modalityIndex, { registration_settings: updated });
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
  <div v-if="sortedEntries.length > 0" class="space-y-3">
    <div v-for="[key, spec] in sortedEntries" :key="key" class="flex items-center justify-between gap-4">
      <label class="text-sm flex-shrink-0 min-w-[180px]">{{ formatLabel(key) }}</label>

      <!-- Bool toggle -->
      <label v-if="spec.type === 'bool'" class="relative inline-flex items-center cursor-pointer">
        <input
          type="checkbox"
          :checked="Boolean(settings[key] ?? spec.default)"
          @change="updateSetting(key, ($event.target as HTMLInputElement).checked)"
          class="sr-only peer"
        />
        <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:bg-blue-600 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
      </label>

      <!-- Enum dropdown -->
      <select
        v-else-if="spec.type === 'enum'"
        :value="settings[key] ?? spec.default"
        @change="updateSetting(key, ($event.target as HTMLSelectElement).value)"
        class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600 w-40"
      >
        <option v-for="opt in spec.options" :key="opt" :value="opt">{{ store.displayName(opt) }}</option>
      </select>

      <!-- Int / Float input -->
      <input
        v-else-if="spec.type === 'int' || spec.type === 'float'"
        type="number"
        :step="spec.type === 'int' ? 1 : 0.01"
        :value="settings[key] ?? spec.default ?? ''"
        :placeholder="spec.nullable ? 'optional' : String(spec.default)"
        @change="updateSetting(key, parseNumeric(spec, ($event.target as HTMLInputElement).value))"
        class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600 w-40"
      />

      <!-- String input -->
      <input
        v-else
        type="text"
        :value="settings[key] ?? spec.default ?? ''"
        :placeholder="spec.nullable ? 'optional' : ''"
        @change="updateSetting(key, ($event.target as HTMLInputElement).value || null)"
        class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600 w-40"
      />
    </div>
  </div>
</template>