<script setup lang="ts">
/**
 * Right-edge panel of the landmark and exclude tools. Lists the pins with
 * their leave-one-out error (how far each pin lies from where the others
 * predict it: a large one marks a pin on the wrong structure), sets the
 * spline stiffness, picks the layer exclusions are drawn on, and warns when
 * the warp folds tissue. Shown while one of the two tools is active, or
 * whenever the warp folds tissue.
 */
import { computed } from 'vue';
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import RangeSlider from '@focus/ui/components/ui/RangeSlider.vue';
import SegmentedControl, { type Segment } from '@focus/ui/components/ui/SegmentedControl.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import Banner from '@focus/ui/components/ui/Banner.vue';
import { useMainStore } from '../../store/main';

const store = useMainStore();

const visible = computed(() =>
  store.targetMeta?.modality_type === 'SPOT'
  && (store.controlMode === 'landmark' || store.controlMode === 'exclude' || store.foldCount > 0));

const LAYERS: Segment<'moving' | 'fixed'>[] = [
  { value: 'moving', label: 'Moving layer' },
  { value: 'fixed', label: 'Fixed layer' },
];

/** Stiffness slider on a log scale: 10^-4 (follows every pin) to 10 (nearly the matrix alone). */
const stiffness = computed({
  get: () => Math.log10(Math.max(store.smoothing, 1e-4)),
  set: (v: number) => store.setSmoothing(10 ** v),
});
const stiffnessLabel = computed(() => store.smoothing.toPrecision(2));

const pins = computed(() => store.landmarks.map((_, i) => ({
  n: i + 1,
  error: store.landmarkErrors[i],
  flagged: store.landmarkFlags[i] ?? false,
})));

const exclusionCount = computed(() => store.exclusions.moving.length + store.exclusions.fixed.length);

const fmt = (e: number | undefined) => (e === undefined || !Number.isFinite(e) ? '–' : `${e.toFixed(1)} px`);
</script>

<template>
  <ChromeSurface v-if="visible" shape="panel" padding="md" class="flex w-72 flex-col gap-3" aria-label="Landmarks">
    <template v-if="store.controlMode !== 'exclude'">
      <div class="flex items-baseline justify-between">
        <span class="type-headline">Landmarks</span>
        <span class="type-footnote nums text-fg2">{{ store.landmarks.length }}</span>
      </div>
      <p class="type-footnote text-fg2">
        Press a structure on the moving layer and drag it onto its counterpart: the tissue around it
        bends while the layer's outline stays put. A click without a drag holds that area in place.
        Right-click a pin to remove it.
      </p>
      <p v-if="!store.landmarks.length" class="type-footnote text-fg2">
        Place the layer roughly with the Aligner first; pins then correct local differences.
      </p>
      <ol v-if="pins.length" class="pin-list flex flex-col gap-1">
        <li v-for="p in pins" :key="p.n" class="flex justify-between type-footnote nums" :class="p.flagged ? 'text-warning-fg' : 'text-fg1'">
          <span>Pin {{ p.n }}</span>
          <span :title="'Distance from where the other pins place it (fixed-layer display pixels)'">{{ fmt(p.error) }}</span>
        </li>
      </ol>
      <RangeSlider v-model="stiffness" label="Stiffness" :min="-4" :max="1" :step="0.25">
        <template #value>{{ stiffnessLabel }}</template>
      </RangeSlider>
      <BaseButton icon="trash" :disabled="!store.landmarks.length" @click="store.clearLandmarks()">
        Clear landmarks
      </BaseButton>
    </template>
    <template v-else>
      <div class="flex items-baseline justify-between">
        <span class="type-headline">Excluded regions</span>
        <span class="type-footnote nums text-fg2">{{ exclusionCount }}</span>
      </div>
      <p class="type-footnote text-fg2">
        Lasso tissue with no counterpart in the other section. Spots there are left unplaced.
        Right-click a region to remove it.
      </p>
      <SegmentedControl v-model="store.excludeLayer" :segments="LAYERS" label="Draw on" />
      <BaseButton icon="trash" :disabled="!exclusionCount" @click="store.clearExclusions()">
        Clear regions
      </BaseButton>
    </template>
    <Banner v-if="store.foldCount > 0" tone="danger" title="The warp folds tissue">
      Some pins cross each other or sit on mismatched structures (shaded red).
      Move or remove them, or raise the stiffness.
    </Banner>
  </ChromeSurface>
</template>

<style scoped>
.pin-list {
  max-height: 12rem;
  overflow-y: auto;
}
</style>
