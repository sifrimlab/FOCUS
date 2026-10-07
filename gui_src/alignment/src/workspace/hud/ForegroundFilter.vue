<script setup lang="ts">
/**
 * Which spots of a layer to show by tissue foreground state. Microgrid
 * layers only carry foreground spots, so they get an explanatory note.
 */
import SegmentedControl, { type Segment } from '@focus/ui/components/ui/SegmentedControl.vue';
import type { ForegroundMode } from '../../store/main';
import { useLayer, type LayerRole } from '../../composables/useLayer';

const props = defineProps<{ role: LayerRole }>();
const layer = useLayer(props.role);

const SEGMENTS: Segment<ForegroundMode>[] = [
  { value: 'all', label: 'All' },
  { value: 'foreground', label: 'Foreground' },
  { value: 'background', label: 'Background' },
];
</script>

<template>
  <p v-if="layer.meta?.foreground_only" class="type-footnote text-fg2">
    Microgrid: only foreground spots are shown, at their exact positions. The alignment applies to all spots.
  </p>
  <div v-else class="flex flex-col gap-1.5">
    <span class="type-footnote text-fg2">Spots</span>
    <SegmentedControl v-model="layer.foregroundMode" :segments="SEGMENTS" label="Spots shown" />
  </div>
</template>
