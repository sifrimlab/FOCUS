<script setup lang="ts" generic="T extends string">
/**
 * Segmented control with a sliding thumb (DESIGN.md 8.11).
 * Segments have equal size; the thumb is translated by the selected index.
 * `orientation="vertical"` stacks the segments (tool rails); `size="lg"`
 * gives them the large control height, matching large icon buttons.
 */
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';
import type { IconName } from '../../icons/paths';

export interface Segment<V extends string> {
  value: V;
  label: string;
  icon?: IconName;
}

const model = defineModel<T>({ required: true });

const props = defineProps<{
  segments: Segment<T>[];
  label: string;
  iconOnly?: boolean;
  orientation?: 'horizontal' | 'vertical';
  size?: 'md' | 'lg';
}>();

const index = computed(() => Math.max(0, props.segments.findIndex(s => s.value === model.value)));
</script>

<template>
  <div
    role="radiogroup"
    :aria-label="label"
    class="segmented"
    :class="{ 'segmented--vertical': orientation === 'vertical', 'segmented--lg': size === 'lg' }"
    :aria-orientation="orientation"
    :style="{ '--count': segments.length, '--index': index }"
  >
    <span class="segmented__thumb" aria-hidden="true" />
    <button
      v-for="seg in segments"
      :key="seg.value"
      type="button"
      role="radio"
      class="segmented__item"
      :class="{ 'segmented__item--icon': iconOnly }"
      :aria-checked="seg.value === model"
      :aria-label="seg.label"
      :title="seg.label"
      @click="model = seg.value"
    >
      <AppIcon v-if="seg.icon" :name="seg.icon" />
      <span v-if="!iconOnly" class="type-caption">{{ seg.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.segmented {
  position: relative;
  display: inline-grid;
  grid-template-columns: repeat(var(--count), 1fr);
  padding: 2px;
  border-radius: var(--radius-full);
  background: var(--mat-inset-fill);
}
.segmented__thumb {
  position: absolute;
  top: 2px;
  bottom: 2px;
  left: 2px;
  width: calc((100% - 4px) / var(--count));
  border-radius: var(--radius-full);
  background: var(--mat-content-fill);
  box-shadow: var(--mat-hairline), var(--elev-1);
  transform: translateX(calc(var(--index) * 100%));
  transition: transform var(--dur-base) var(--ease-spring);
}
.segmented__item {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px;
  border-radius: var(--radius-full);
  color: var(--fg3);
  transition: color var(--dur-fast) var(--ease-out);
}
.segmented__item--icon { width: 26px; padding: 0; }
/* Track padding (2px each side) + item = --control-lg. */
.segmented--lg .segmented__item { height: calc(var(--control-lg) - 4px); }
.segmented--lg .segmented__item--icon { width: calc(var(--control-lg) - 4px); }

.segmented--vertical {
  grid-template-columns: 1fr;
  grid-template-rows: repeat(var(--count), 1fr);
}
.segmented--vertical .segmented__thumb {
  right: 2px;
  width: auto;
  bottom: auto;
  height: calc((100% - 4px) / var(--count));
  transform: translateY(calc(var(--index) * 100%));
}
.segmented__item:hover { color: var(--fg1); }
.segmented__item[aria-checked="true"] { color: var(--fg1); }
</style>
