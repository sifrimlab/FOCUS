<script setup lang="ts">
/** Top-center identity pill: app, current sample, and progress through the dataset. */
import { computed } from 'vue';
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import ProgressBar from '@focus/ui/components/ui/ProgressBar.vue';
import Separator from '@focus/ui/components/ui/Separator.vue';
import BrandMark from '@focus/ui/components/shell/BrandMark.vue';
import { useMainStore } from '../../store/main';

const store = useMainStore();

const progress = computed(() => {
  if (!store.sampleInfo) return 0;
  return (store.sampleInfo.sample_index / store.sampleInfo.total_samples_count) * 100;
});
</script>

<template>
  <ChromeSurface aria-label="Current sample">
    <div class="flex h-(--control-md) items-center gap-2 px-2 whitespace-nowrap">
      <BrandMark :size="20" />
      <span class="type-headline text-fg1">Alignment</span>
      <Separator />
      <span class="type-mono text-fg1 ellipsis min-w-0 max-w-64" :title="store.sampleInfo?.sample_id">{{ store.sampleInfo?.sample_id }}</span>
      <span class="w-16 shrink-0"><ProgressBar :value="progress" label="Samples aligned" :active="false" /></span>
      <span class="type-footnote nums text-fg2">{{ store.sampleInfo?.sample_index }} of {{ store.sampleInfo?.total_samples_count }}</span>
    </div>
  </ChromeSurface>
</template>
