<script setup lang="ts">
/**
 * View of a clustered spot layer: clusters at a resolution set here (the fine
 * clusters merged along their stored merge order), or one feature image (an
 * ion or a gene), coloured low to high. Shown only when the layer has a merge
 * tree; the feature switch only when it carries feature images.
 */
import { computed } from 'vue';
import RangeSlider from '@focus/ui/components/ui/RangeSlider.vue';
import SegmentedControl, { type Segment } from '@focus/ui/components/ui/SegmentedControl.vue';
import SelectField from '@focus/ui/components/ui/SelectField.vue';
import { useMainStore } from '../../store/main';
import type { LayerRole } from '../../composables/useLayer';
import { magma, treeOf, type SpotViewMode } from '../../utils/spotDisplay';

const props = defineProps<{ role: LayerRole }>();
const store = useMainStore();

const meta = computed(() => (props.role === 'target' ? store.targetMeta : store.referenceMeta));
const view = computed(() => store.spotViews[props.role]);
const tree = computed(() => treeOf(meta.value));
const features = computed(() => meta.value?.feature_images ?? []);

const MODES: Segment<SpotViewMode>[] = [
  { value: 'clusters', label: 'Clusters' },
  { value: 'features', label: 'Feature images' },
];

const mode = computed({
  get: () => view.value?.mode ?? 'clusters',
  set: (m: SpotViewMode) => store.setSpotView(props.role, { mode: m }),
});
const level = computed({
  get: () => view.value?.level ?? 1,
  set: (v: number) => store.setSpotView(props.role, { level: Math.round(v) }),
});
const feature = computed({
  get: () => String(view.value?.feature ?? 0),
  set: (v: string) => store.setSpotView(props.role, { feature: Number(v) }),
});
const ramp = `linear-gradient(to right, ${Array.from({ length: 9 }, (_, k) => magma(k / 8)).join(', ')})`;
</script>

<template>
  <section v-if="view && tree" class="flex flex-col gap-2" aria-label="Spot view">
    <SegmentedControl v-if="features.length" v-model="mode" :segments="MODES" label="Spot view" />
    <RangeSlider
      v-if="mode === 'clusters'"
      v-model="level"
      label="Clusters"
      :min="Math.min(2, tree.nFine)"
      :max="tree.nFine"
      :step="1"
    >
      <template #value>{{ level }} of {{ tree.nFine }}</template>
    </RangeSlider>
    <template v-else>
      <SelectField v-model="feature" aria-label="Feature image">
        <option v-for="(label, i) in features" :key="i" :value="String(i)">{{ label }}</option>
      </SelectField>
      <div class="flex items-center gap-2 type-footnote text-fg2">
        <span>low</span>
        <span class="ramp" :style="{ background: ramp }" aria-hidden="true" />
        <span>high</span>
      </div>
    </template>
  </section>
</template>

<style scoped>
.ramp {
  flex: 1;
  height: 6px;
  border-radius: var(--radius-full);
}
</style>
