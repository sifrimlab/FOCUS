<script setup lang="ts">
/**
 * Live status of the running stage: current modality, sample progress,
 * sub-step tracker with item progress, and the latest message.
 */
import { computed } from 'vue';
import type { PipelineStatus } from '../../api/types';
import GlassCard from '../ui/GlassCard.vue';
import CardHeader from '../ui/CardHeader.vue';
import StatusPill from '../ui/StatusPill.vue';
import ProgressBar from '../ui/ProgressBar.vue';
import StepDots from './StepDots.vue';
import StatusLine from './StatusLine.vue';

const MAX_STEP_DOTS = 9;

const props = defineProps<{ status: PipelineStatus }>();
const s = computed(() => props.status);

const title = computed(() => {
  const stage = s.value.stage?.replace(/_/g, ' ') ?? 'Initializing';
  return stage.charAt(0).toUpperCase() + stage.slice(1);
});

const hasSampleProgress = computed(() => s.value.total_samples > 0);
const hasStepDots = computed(() => s.value.sub_step_total > 0 && s.value.sub_step_total <= MAX_STEP_DOTS);
const hasItemProgress = computed(() => s.value.sub_step_items_total > 0);

const percent = (done: number, total: number) => Math.min(100, Math.round((done / total) * 100));

// "3/7 - Normalizing counts" -> "Normalizing counts"
const stepDescription = computed(() => {
  const step = s.value.sub_step;
  if (!step) return '';
  const match = step.match(/^\d+\/\d+\s*[-–]\s*(.*)/);
  return match ? match[1] : step;
});
</script>

<template>
  <GlassCard class="flex flex-col gap-5">
    <CardHeader :title="title">
      <template v-if="s.current_modality" #actions>
        <StatusPill>
          {{ s.current_modality }}
          <span v-if="s.total_modalities > 0" class="nums text-fg3">{{ s.current_modality_index }}/{{ s.total_modalities }}</span>
        </StatusPill>
      </template>
    </CardHeader>

    <div v-if="hasSampleProgress" class="flex flex-col gap-2">
      <div class="flex items-baseline justify-between gap-3">
        <div class="flex min-w-0 items-baseline gap-2">
          <span class="type-footnote text-fg3 shrink-0">Sample</span>
          <span class="type-mono text-fg1 ellipsis">{{ s.current_sample }}</span>
        </div>
        <span class="type-footnote nums text-fg3 shrink-0">{{ s.current_sample_index }} / {{ s.total_samples }}</span>
      </div>
      <ProgressBar
        tone="data-2"
        label="Sample progress"
        :value="percent(s.current_sample_index, s.total_samples)"
        :active="false"
      />
    </div>

    <div v-if="s.sub_step" class="flex flex-col gap-2">
      <StepDots v-if="hasStepDots" :total="s.sub_step_total" :current="s.sub_step_index" class="mb-2" />
      <div class="flex items-baseline justify-between gap-3">
        <p class="type-body-strong text-fg1 ellipsis">{{ stepDescription || s.sub_step }}</p>
        <p v-if="hasItemProgress" class="type-footnote nums text-fg3 shrink-0">
          {{ s.sub_step_progress }} / {{ s.sub_step_items_total }}
        </p>
      </div>
      <ProgressBar
        label="Step progress"
        :value="hasItemProgress ? percent(s.sub_step_progress, s.sub_step_items_total) : null"
      />
    </div>

    <StatusLine :message="s.message || 'Starting…'" />
  </GlassCard>
</template>
