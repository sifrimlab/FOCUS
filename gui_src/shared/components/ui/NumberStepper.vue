<script setup lang="ts">
/**
 * Labelled numeric field with step buttons (DESIGN.md 8.18): label, optional
 * reset (when `resetLabel` is given), minus, field, plus. The step buttons
 * repeat while held (mouse) or step once per Enter/Space (keyboard).
 *
 * Every keystroke emits `update:modelValue`, even when the parsed value equals
 * the current one (unlike defineModel), so a setter with side effects runs
 * exactly as with `v-model.number` on a plain input.
 */
import { ref } from 'vue';
import IconButton from './IconButton.vue';
import TextField from './TextField.vue';
import { useHoldRepeat } from '../../composables/useHoldRepeat';

defineProps<{
  modelValue: string | number | null;
  label: string;
  step?: number | string;
  unit?: string;
  resetLabel?: string;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: number | string];
  step: [direction: -1 | 1];
  reset: [];
}>();

// Same coercion as v-model.number: the parsed number, or the raw text if it does not parse.
const onInput = (e: Event) => {
  const raw = (e.target as HTMLInputElement).value;
  const n = parseFloat(raw);
  emit('update:modelValue', isNaN(n) ? raw : n);
};

const DIRECTIONS = [-1, 1] as const;

const hold = useHoldRepeat();
const press = (direction: -1 | 1) => hold.start(() => emit('step', direction));

// The field sits inside the v-for, so a function ref keeps a single instance.
const field = ref<InstanceType<typeof TextField> | null>(null);
const setField = (el: unknown) => { field.value = el as InstanceType<typeof TextField> | null; };

defineExpose({
  focus: () => { field.value?.focus(); field.value?.select(); },
});
</script>

<template>
  <div class="flex items-center gap-1" role="group" :aria-label="label">
    <span class="type-footnote text-fg2 px-1.5">{{ label }}</span>
    <IconButton v-if="resetLabel" icon="arrow-path" :label="resetLabel" size="sm" round @click="emit('reset')" />
    <template v-for="direction in DIRECTIONS" :key="direction">
      <TextField v-if="direction > 0" :ref="setField" :model-value="modelValue" type="number" :step="step" :aria-label="label" mono class="w-24" @input="onInput">
        <template v-if="unit" #suffix><span class="type-footnote text-fg3">{{ unit }}</span></template>
      </TextField>
      <IconButton
        :icon="direction < 0 ? 'minus' : 'plus'"
        :label="`${direction < 0 ? 'Decrease' : 'Increase'} ${label.toLowerCase()}`"
        size="sm"
        round
        @mousedown="press(direction)"
        @mouseup="hold.stop"
        @mouseleave="hold.stop"
        @keydown.enter.prevent="emit('step', direction)"
        @keydown.space.prevent="emit('step', direction)"
      />
    </template>
  </div>
</template>
