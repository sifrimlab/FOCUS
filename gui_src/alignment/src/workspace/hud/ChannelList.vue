<script setup lang="ts">
/** Channel visibility of a multichannel image layer: one chip per channel with its colour, plus all/none. */
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import InsetWell from '@focus/ui/components/ui/InsetWell.vue';
import ToggleChip from '@focus/ui/components/ui/ToggleChip.vue';
import { useLayer, type LayerRole } from '../../composables/useLayer';

const props = defineProps<{ role: LayerRole }>();

const layer = useLayer(props.role);

const isShown = (index: number) => layer.shownChannels.includes(index);
const toggle = (index: number) => {
  const shown = isShown(index)
    ? layer.shownChannels.filter(i => i !== index)
    : [...layer.shownChannels, index].sort((a, b) => a - b);
  layer.setChannels(shown);
};
const showAll = () => layer.setChannels(layer.channels.map((_, i) => i));
const showNone = () => layer.setChannels([]);
</script>

<template>
  <section class="flex flex-col gap-1.5" aria-label="Channels">
    <div class="flex items-center justify-between">
      <span class="type-footnote text-fg2">Channels</span>
      <div class="flex">
        <BaseButton variant="plain" @click="showAll">All</BaseButton>
        <BaseButton variant="plain" @click="showNone">None</BaseButton>
      </div>
    </div>
    <InsetWell padding="sm" class="flex max-h-36 flex-wrap gap-1 overflow-y-auto">
      <ToggleChip
        v-for="(channel, index) in layer.channels"
        :key="index"
        :model-value="isShown(index)"
        :data-channel="index"
        @update:model-value="toggle(index)"
      >
        <span class="swatch" :style="{ background: `#${channel.color}` }" aria-hidden="true" />
        {{ channel.name }}
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
