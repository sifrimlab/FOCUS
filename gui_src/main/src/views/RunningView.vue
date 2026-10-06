<script setup lang="ts">
/**
 * Progress screen: stage rail on the left; on the right the current work
 * (stage, modality, step, samples), prompts, and the activity log. The root
 * carries data-stage so every accent follows the running stage's color.
 */
import { useMainStore } from '../store/main';
import { provideRunProgress } from '../composables/useRunProgress';
import RunHeader from '../components/running/RunHeader.vue';
import StageRail from '../components/running/StageRail.vue';
import CurrentWorkCard from '../components/running/CurrentWorkCard.vue';
import ActivityLog from '../components/running/ActivityLog.vue';
import AlignmentWaitBanner from '../components/running/AlignmentWaitBanner.vue';
import PipelineErrorBanner from '../components/running/PipelineErrorBanner.vue';

const store = useMainStore();
const run = provideRunProgress();
</script>

<template>
  <div
    class="mx-auto flex w-full max-w-6xl flex-col gap-8 px-6 pt-20 pb-12"
    :data-stage="run.activeStage.value?.id ?? run.status.value.stage ?? undefined"
  >
    <RunHeader />

    <div class="run-grid grid items-start gap-6">
      <div class="rail-col">
        <StageRail />
      </div>

      <div class="flex min-w-0 flex-col gap-6">
        <Transition name="fade">
          <AlignmentWaitBanner
            v-if="run.status.value.state === 'alignment_waiting'"
            :port="run.status.value.alignment_port"
          />
        </Transition>
        <Transition name="fade">
          <PipelineErrorBanner
            v-if="run.status.value.state === 'error'"
            :error="run.status.value.error"
            @back="store.goToConfig()"
          />
        </Transition>
        <CurrentWorkCard />
        <ActivityLog />
      </div>
    </div>
  </div>
</template>

<style scoped>
.run-grid { grid-template-columns: minmax(0, 1fr); }
@media (min-width: 900px) {
  .run-grid { grid-template-columns: minmax(260px, 300px) minmax(0, 1fr); }
  .rail-col { position: sticky; top: 24px; }
}
</style>
