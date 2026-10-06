<script setup lang="ts">
/** Running: stage stepper, live status, and alignment or error prompts. */
import { computed } from 'vue';
import { useMainStore } from '../store/main';
import StageProgress, { type Stage } from '../components/running/StageProgress.vue';
import RunStatusCard from '../components/running/RunStatusCard.vue';
import AlignmentWaitBanner from '../components/running/AlignmentWaitBanner.vue';
import PipelineErrorBanner from '../components/running/PipelineErrorBanner.vue';

const store = useMainStore();
const status = computed(() => store.pipelineStatus);

// Stages that apply to this configuration, in execution order.
const stages = computed<Stage[]>(() => {
  const cfg = store.config;
  const list: Stage[] = [{ name: 'preprocessing', label: 'Preprocessing' }];
  if (cfg.perform_alignment && cfg.modalities.length >= 2) list.push({ name: 'alignment', label: 'Alignment' });
  if (cfg.spatial_annotations !== null) list.push({ name: 'annotation_transfer', label: 'Annotation transfer' });
  if (cfg.perform_registration) {
    list.push({ name: 'registration', label: 'Registration' });
    list.push({ name: 'compiling', label: 'Compiling' });
  }
  return list;
});
</script>

<template>
  <div class="flex min-h-full flex-col items-center justify-center px-4 py-16">
    <div class="flex w-full max-w-2xl flex-col gap-5">
      <StageProgress :current-stage="status.stage" :stages="stages" />
      <RunStatusCard :status="status" />

      <Transition name="fade">
        <AlignmentWaitBanner v-if="status.state === 'alignment_waiting'" :port="status.alignment_port" />
      </Transition>
      <Transition name="fade">
        <PipelineErrorBanner v-if="status.state === 'error'" :error="status.error" @back="store.goToConfig()" />
      </Transition>
    </div>
  </div>
</template>
