<script setup lang="ts">
/**
 * Floating panel of one layer. The header (identity dot, name, type) is
 * always visible and toggles the details; the moving layer also keeps its
 * opacity slider visible, since it is the most used layer control.
 */
import { computed } from 'vue';
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import Disclosure from '@focus/ui/components/ui/Disclosure.vue';
import RangeSlider from '@focus/ui/components/ui/RangeSlider.vue';
import TagLabel from '@focus/ui/components/ui/TagLabel.vue';
import { useMainStore } from '../../store/main';
import { useUiStore } from '../../store/ui';
import { useLayer, type LayerRole } from '../../composables/useLayer';
import ClassFilterList from './ClassFilterList.vue';
import ForegroundFilter from './ForegroundFilter.vue';
import SpotSizeFields from './SpotSizeFields.vue';

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
      <div v-if="layer.isSpot" class="flex flex-col gap-3">
        <ClassFilterList :role="role" />
        <ForegroundFilter :role="role" />
        <SpotSizeFields :role="role" />
      </div>
      <p v-else class="type-footnote nums text-fg3">
        {{ layer.meta?.image_shape?.join(' × ') }} px
      </p>
    </Disclosure>
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
