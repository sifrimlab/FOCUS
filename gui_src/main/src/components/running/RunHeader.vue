<script setup lang="ts">
/** Page header of the progress screen: title, run state, total elapsed time. */
import { computed } from 'vue';
import { injectRunProgress } from '../../composables/useRunProgress';
import { formatDuration } from '../../utils/duration';
import StatusPill from '../ui/StatusPill.vue';

const run = injectRunProgress();

const pill = computed(() => {
  switch (run.status.value.state) {
    case 'alignment_waiting': return { tone: 'warning' as const, label: 'Waiting for alignment', live: true };
    case 'error': return { tone: 'danger' as const, label: 'Stopped with an error', live: false };
    case 'completed': return { tone: 'success' as const, label: 'Completed', live: false };
    default: return { tone: 'stage' as const, label: 'Running', live: true };
  }
});
</script>

<template>
  <header class="flex flex-wrap items-end justify-between gap-4">
    <div class="flex flex-col gap-1">
      <h1 class="type-title-1 text-fg1">Processing</h1>
      <p class="type-callout text-fg3">Progress is updated live. You can leave this page open while the run continues.</p>
    </div>
    <div class="flex items-center gap-3">
      <StatusPill :tone="pill.tone" :live="pill.live">{{ pill.label }}</StatusPill>
      <span class="type-headline nums text-fg1" :title="'Total elapsed time'">
        {{ run.status.value.run_started_at ? formatDuration(run.runSeconds.value) : '…' }}
      </span>
    </div>
  </header>
</template>
