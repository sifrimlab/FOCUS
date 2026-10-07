<script setup lang="ts">
/**
 * Labelled range slider (DESIGN.md 8.18): accent fill up to the thumb on an
 * inset track; the thumb matches the segmented-control thumb. The `value`
 * slot shows a formatted readout in tabular figures.
 */
import { computed } from 'vue';

const model = defineModel<number>({ required: true });

const props = withDefaults(defineProps<{
  label: string;
  min?: number;
  max?: number;
  step?: number;
}>(), { min: 0, max: 1, step: 0.05 });

const fill = computed(() => `${((Number(model.value) - props.min) / (props.max - props.min)) * 100}%`);
</script>

<template>
  <label class="flex items-center gap-2">
    <span class="type-caption text-fg3">{{ label }}</span>
    <input
      v-model.number="model"
      type="range"
      class="range"
      :min="min"
      :max="max"
      :step="step"
      :style="{ '--fill': fill }"
    />
    <span v-if="$slots.value" class="type-footnote nums text-fg3 range__value"><slot name="value" /></span>
  </label>
</template>

<style scoped>
.range {
  flex: 1;
  min-width: 0;
  height: var(--control-sm);
  appearance: none;
  background: transparent;
  cursor: pointer;
}
.range::-webkit-slider-runnable-track {
  height: 4px;
  border-radius: var(--radius-full);
  background: linear-gradient(to right, var(--primary) var(--fill), var(--mat-inset-fill) var(--fill));
}
.range::-moz-range-track {
  height: 4px;
  border-radius: var(--radius-full);
  background: var(--mat-inset-fill);
}
.range::-moz-range-progress {
  height: 4px;
  border-radius: var(--radius-full);
  background: var(--primary);
}
.range::-webkit-slider-thumb {
  appearance: none;
  width: 16px;
  height: 16px;
  margin-top: -6px;
  border-radius: var(--radius-full);
  background: var(--mat-content-fill);
  box-shadow: var(--mat-hairline), var(--elev-1);
  transition: transform var(--dur-base) var(--ease-spring-snappy);
}
.range::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border: none;
  border-radius: var(--radius-full);
  background: var(--mat-content-fill);
  box-shadow: var(--mat-hairline), var(--elev-1);
}
.range:active::-webkit-slider-thumb { transform: scale(1.1); }
.range:focus-visible { outline: none; }
.range:focus-visible::-webkit-slider-thumb { box-shadow: var(--mat-hairline), var(--focus-ring); }
.range:focus-visible::-moz-range-thumb { box-shadow: var(--mat-hairline), var(--focus-ring); }
.range__value { min-width: 4ch; text-align: right; }
</style>
