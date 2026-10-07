<script setup lang="ts">
/** Cluster visibility of a spot layer: one chip per class with its color, plus all/none. */
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import InsetWell from '@focus/ui/components/ui/InsetWell.vue';
import ToggleChip from '@focus/ui/components/ui/ToggleChip.vue';
import { useLayer, type LayerRole } from '../../composables/useLayer';
import { useClassFilter } from '../../composables/useClassFilter';

const props = defineProps<{ role: LayerRole }>();

const layer = useLayer(props.role);
const filter = useClassFilter(layer);
</script>

<template>
  <section class="flex flex-col gap-1.5" aria-label="Clusters">
    <div class="flex items-center justify-between">
      <span class="type-footnote text-fg2">Clusters</span>
      <div class="flex">
        <BaseButton variant="plain" @click="filter.showAll">All</BaseButton>
        <BaseButton variant="plain" @click="filter.showNone">None</BaseButton>
      </div>
    </div>
    <InsetWell padding="sm" class="flex max-h-36 flex-wrap gap-1 overflow-y-auto">
      <ToggleChip
        v-for="cls in layer.classes"
        :key="cls"
        :model-value="filter.isShown(cls)"
        :data-class="cls"
        @update:model-value="filter.toggle(cls)"
      >
        <span class="swatch" :style="{ background: layer.colorOf(cls) }" aria-hidden="true" />
        {{ cls }}
      </ToggleChip>
    </InsetWell>
  </section>
</template>

<style scoped>
.swatch {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 4px;
  border-radius: var(--radius-full);
  box-shadow: var(--mat-hairline);
}
</style>
