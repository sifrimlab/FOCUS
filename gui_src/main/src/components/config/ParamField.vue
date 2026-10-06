<script setup lang="ts">
/**
 * One control for one schema parameter, chosen by spec type: toggle, select,
 * number, text or file picker. Text and number fields keep a local draft and
 * commit on change, like the native inputs they replace.
 */
import { computed, ref, watch } from 'vue';
import type { ParamSpec } from '../../api/types';
import { useMainStore } from '../../store/main';
import { parseParamValue, type EmptyTextPolicy } from '../../utils/params';
import ToggleSwitch from '../ui/ToggleSwitch.vue';
import SelectField from '../ui/SelectField.vue';
import TextField from '../ui/TextField.vue';
import FilePicker from '../browser/FilePicker.vue';

const props = defineProps<{
  name: string;
  label: string;
  spec: ParamSpec;
  value: unknown;
  emptyText: EmptyTextPolicy;
}>();

const emit = defineEmits<{ commit: [value: unknown] }>();

const store = useMainStore();

const current = computed(() => props.value ?? props.spec.default);
const isNumber = computed(() => props.spec.type === 'int' || props.spec.type === 'float');
const placeholder = computed(() => {
  if (props.spec.nullable) return 'optional';
  return isNumber.value ? String(props.spec.default) : '';
});

const draft = ref<string | number>('');
watch(current, v => { draft.value = v === null || v === undefined ? '' : String(v); }, { immediate: true });

const commitDraft = () => emit('commit', parseParamValue(props.spec, String(draft.value), props.emptyText));
</script>

<template>
  <ToggleSwitch
    v-if="spec.type === 'bool'"
    :model-value="Boolean(current)"
    :label="label"
    @update:model-value="emit('commit', $event)"
  />

  <SelectField
    v-else-if="spec.type === 'enum'"
    class="w-56"
    :model-value="String(current ?? '')"
    :aria-label="label"
    @update:model-value="emit('commit', $event)"
  >
    <option v-for="opt in spec.options" :key="opt" :value="opt">{{ store.displayName(opt) }}</option>
  </SelectField>

  <FilePicker
    v-else-if="spec.type === 'path'"
    :value="current ?? null"
    @update:value="emit('commit', $event)"
  />

  <TextField
    v-else
    v-model="draft"
    class="w-56"
    :type="isNumber ? 'number' : 'text'"
    :step="spec.type === 'int' ? 1 : spec.type === 'float' ? 0.01 : undefined"
    :placeholder="placeholder"
    :aria-label="label"
    :name="name"
    @change="commitDraft"
  />
</template>
