<script setup lang="ts">
/** Icon from the registry in icons/paths.ts. Color follows currentColor. */
import { computed } from 'vue';
import { FILLED_ICONS, OUTLINE_ICONS, isFilledIcon, type IconName } from '../../icons/paths';

const props = withDefaults(defineProps<{
  name: IconName;
  size?: 12 | 16 | 20 | 24 | 40;
}>(), { size: 16 });

const filled = computed(() => isFilledIcon(props.name));
const d = computed(() =>
  isFilledIcon(props.name) ? FILLED_ICONS[props.name] : OUTLINE_ICONS[props.name],
);
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    aria-hidden="true"
    class="shrink-0"
    :fill="filled ? 'currentColor' : 'none'"
    :stroke="filled ? 'none' : 'currentColor'"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path :d="d" />
  </svg>
</template>
