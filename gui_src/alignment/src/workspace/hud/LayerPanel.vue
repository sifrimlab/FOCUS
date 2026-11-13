<script setup lang="ts">
/**
 * Floating panel of one layer. The header (identity dot, name, type) is
 * always visible and toggles the details; a clustered spot layer keeps its
 * view controls (resolution slider, feature images) visible, and the moving
 * layer its opacity slider, since they are the most used layer controls.
 */
import { computed } from 'vue';
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import Disclosure from '@focus/ui/components/ui/Disclosure.vue';
import RangeSlider from '@focus/ui/components/ui/RangeSlider.vue';
import TagLabel from '@focus/ui/components/ui/TagLabel.vue';
import { useMainStore } from '../../store/main';
import { useUiStore } from '../../store/ui';
import { useLayer, type LayerRole } from '../../composables/useLayer';
import ChannelList from './ChannelList.vue';
import ClassFilterList from './ClassFilterList.vue';
import ForegroundFilter from './ForegroundFilter.vue';
import SpotSizeFields from './SpotSizeFields.vue';
import SpotViewControls from './SpotViewControls.vue';

const props = defineProps<{ role: LayerRole }>();

const store = useMainStore();
const ui = useUiStore();
const layer = useLayer(props.role);

const ROLE_HINT: Record<LayerRole, string> = { target: 'Moving layer', reference: 'Fixed layer' };
const opacityPercent = computed(() => (store.targetOpacity * 100).toFixed(0));
</script>

<template>
  <ChromeSurface shape="panel" padding="md" class="flex w-80 flex-col gap-3" :data-layer="role" :aria-label="ROLE_HINT[role]">
    <Disclosure v-model:open="ui.expanded[role]" :title="layer.name">
      <template #summary>
        <span class="dot" :class="`dot--${role}`" :title="ROLE_HINT[role]" aria-hidden="true" />
        <span class="ellipsis" :title="layer.name">{{ layer.name }}</span>
        <TagLabel>{{ layer.meta?.modality_type }}</TagLabel>
      </template>
      <div v-if="layer.isSpot" class="flex flex-col gap-4">
        <ClassFilterList v-if="!layer.display || layer.display.filterable" :role="role" />
        <ForegroundFilter :role="role" />
        <SpotSizeFields :role="role" />
      </div>
      <div v-else class="flex flex-col gap-4">
        <ChannelList v-if="layer.channels.length" :role="role" />
        <p class="type-footnote nums text-fg2">
          {{ layer.meta?.image_shape?.join(' × ') }} px
        </p>
      </div>
    </Disclosure>
    <SpotViewControls v-if="layer.isSpot" :role="role" />
    <RangeSlider v-if="role === 'target'" v-model="store.targetOpacity" label="Opacity">
      <template #value>{{ opacityPercent }}%</template>
    </RangeSlider>
  </ChromeSurface>
</template>

<style scoped>
.dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: var(--radius-full);
}
.dot--target { background: var(--layer-target); }
.dot--reference { background: var(--layer-reference); }
</style>
