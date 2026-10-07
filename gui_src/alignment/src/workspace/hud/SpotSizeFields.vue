<script setup lang="ts">
/** Display size of a layer's spots, in µm (width and height). */
import TextField from '@focus/ui/components/ui/TextField.vue';
import { useLayer, type LayerRole } from '../../composables/useLayer';

const props = defineProps<{ role: LayerRole }>();
const layer = useLayer(props.role);

const AXES = [{ index: 0, label: 'Spot width' }, { index: 1, label: 'Spot height' }] as const;
</script>

<template>
  <div class="flex items-center justify-between gap-2">
    <span class="type-caption text-fg3">Spot size</span>
    <div class="flex gap-1.5">
      <TextField
        v-for="axis in AXES"
        :key="axis.index"
        v-model="layer.spotSize[axis.index]"
        type="number"
        mono
        class="w-24"
        :aria-label="axis.label"
        :title="axis.label"
      >
        <template #suffix><span class="type-footnote text-fg3">µm</span></template>
      </TextField>
    </div>
  </div>
</template>
