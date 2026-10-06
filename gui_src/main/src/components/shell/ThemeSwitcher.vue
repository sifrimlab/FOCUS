<script setup lang="ts">
/** System / Light / Dark control bound to useTheme (DESIGN.md 8.11). */
import { computed } from 'vue';
import SegmentedControl, { type Segment } from '../ui/SegmentedControl.vue';
import { useTheme, type ThemePreference } from '../../composables/useTheme';

const { preference, setPreference } = useTheme();

const SEGMENTS: Segment<ThemePreference>[] = [
  { value: 'system', label: 'Use system appearance', icon: 'computer-desktop' },
  { value: 'light', label: 'Light appearance', icon: 'sun' },
  { value: 'dark', label: 'Dark appearance', icon: 'moon' },
];

const model = computed({
  get: () => preference.value,
  set: setPreference,
});
</script>

<template>
  <SegmentedControl v-model="model" :segments="SEGMENTS" label="Appearance" icon-only />
</template>
