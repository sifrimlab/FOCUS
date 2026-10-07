<script setup lang="ts">
/**
 * Labelled numeric field with step buttons (DESIGN.md 8.18). The buttons
 * repeat while held (mouse) or step once per Enter/Space (keyboard). A reset
 * button appears when `resetLabel` is given.
 *
 * Every keystroke emits `update:modelValue`, even when the parsed value equals
 * the current one (unlike defineModel), so a setter with side effects runs
 * exactly as with `v-model.number` on a plain input.
 */
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
</script>

<template>
  <div class="flex items-center gap-1" role="group" :aria-label="label">
    <span class="type-caption text-fg3 px-1.5">{{ label }}</span>
    <template v-for="direction in DIRECTIONS" :key="direction">
      <TextField v-if="direction > 0" :model-value="modelValue" type="number" :step="step" :aria-label="label" mono class="w-24" @input="onInput">
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
    <IconButton v-if="resetLabel" icon="arrow-uturn-left" :label="resetLabel" size="sm" round @click="emit('reset')" />
  </div>
</template>
