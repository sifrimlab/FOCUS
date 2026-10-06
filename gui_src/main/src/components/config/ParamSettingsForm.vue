<script setup lang="ts">
/**
 * Schema-driven settings form, shared by processing and registration
 * settings. Emits the full updated settings object on every commit.
 */
import { computed } from 'vue';
import type { ParamSpecs, EmptyTextPolicy } from '../../utils/params';
import { sortParamEntries } from '../../utils/params';
import { humanizeKey } from '../../utils/format';
import FormRow from '../ui/FormRow.vue';
import ParamField from './ParamField.vue';

const props = defineProps<{
  specs: ParamSpecs;
  settings: Record<string, unknown>;
  emptyText: EmptyTextPolicy;
  /** Parameter keys edited elsewhere (e.g. force_recomputing on Review). */
  exclude?: string[];
}>();

const emit = defineEmits<{ update: [settings: Record<string, unknown>] }>();

const entries = computed(() =>
  sortParamEntries(props.specs).filter(([key]) => !props.exclude?.includes(key)),
);

const commit = (key: string, value: unknown) => emit('update', { ...props.settings, [key]: value });
</script>

<template>
  <div v-if="entries.length > 0" class="flex flex-col gap-3">
    <FormRow v-for="[key, spec] in entries" :key="key" :label="humanizeKey(key)">
      <ParamField
        :name="key"
        :label="humanizeKey(key)"
        :spec="spec"
        :value="settings[key]"
        :empty-text="emptyText"
        @commit="commit(key, $event)"
      />
    </FormRow>
  </div>
</template>
