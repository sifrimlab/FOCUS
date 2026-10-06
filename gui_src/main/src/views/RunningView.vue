<script setup lang="ts">
/**
 * Progress screen: stage rail on the left; on the right the current work
 * (stage, modality, step, samples), prompts, and the activity log. The root
 * carries data-stage so every accent follows the running stage's color.
 * On wide windows the page fits the window and only the activity log scrolls.
 * While the server is unreachable, live motion pauses (data-offline).
 */
import { useMainStore } from '../store/main';
import { provideRunProgress } from '../composables/useRunProgress';
import RunHeader from '../components/running/RunHeader.vue';
import StageRail from '../components/running/StageRail.vue';
import CurrentWorkCard from '../components/running/CurrentWorkCard.vue';
import ActivityLog from '../components/running/ActivityLog.vue';
import AlignmentWaitBanner from '../components/running/AlignmentWaitBanner.vue';
import PipelineErrorBanner from '../components/running/PipelineErrorBanner.vue';
import ConnectionLostBanner from '../components/running/ConnectionLostBanner.vue';

const store = useMainStore();
const run = provideRunProgress();
</script>

<template>
  <div
    class="run-page mx-auto flex w-full max-w-6xl flex-col gap-8 px-6 pt-20 pb-8"
    :data-stage="run.activeStage.value?.id ?? run.status.value.stage ?? undefined"
    :data-offline="run.offline.value || undefined"
  >
    <RunHeader />

    <div class="run-grid grid gap-6">
      <div class="self-start">
        <StageRail />
      </div>

      <div class="work-col flex min-w-0 flex-col gap-6">
        <Transition name="fade">
          <ConnectionLostBanner v-if="run.connection.value === 'lost'" :last-contact-at="store.lastContactAt" />
        </Transition>
        <Transition name="fade">
          <PipelineErrorBanner v-if="run.connection.value === 'run-lost'" title="Run interrupted" @back="store.goToConfig()">
            The server responded, but no pipeline is running: the server was restarted and this run was stopped.
          </PipelineErrorBanner>
        </Transition>
        <Transition name="fade">
          <AlignmentWaitBanner
            v-if="!run.offline.value && run.status.value.state === 'alignment_waiting'"
            :port="run.status.value.alignment_port"
          />
        </Transition>
        <Transition name="fade">
          <PipelineErrorBanner
            v-if="!run.offline.value && run.status.value.state === 'error'"
            :error="run.status.value.error"
            @back="store.goToConfig()"
          />
        </Transition>
        <CurrentWorkCard class="shrink-0" />
        <ActivityLog class="log-card" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.run-page { min-height: 100%; }
.run-grid { grid-template-columns: minmax(0, 1fr); }
/* Shown state is the last known one: stop the motion that implies live progress. */
.run-page[data-offline] :deep(*:not(.keep-live, .keep-live *)) { animation-play-state: paused; }

@media (min-width: 900px) {
  /* Fit the window; below the min-height guard the page scrolls instead of clipping. */
  .run-page { height: 100%; min-height: 600px; }
  .run-grid {
    flex: 1;
    min-height: 0;
    grid-template-columns: minmax(260px, 300px) minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr);
  }
  .work-col { min-height: 0; --log-max-height: none; }
  .log-card { flex: 1; min-height: 160px; }
}
</style>
