<script setup lang="ts">
/** Page header of the progress screen: title, run state, total elapsed time. */
import { computed } from 'vue';
import { injectRunProgress } from '../../composables/useRunProgress';
import { formatDuration } from '../../utils/duration';
import StatusPill from '@focus/ui/components/ui/StatusPill.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import { useMainStore } from '../../store/main';
import { useDialog } from '../../composables/useDialog';

const run = injectRunProgress();
const store = useMainStore();
const { showConfirm } = useDialog();

const pill = computed(() => {
  if (run.connection.value === 'lost') return { tone: 'warning' as const, label: 'Connection lost', live: false };
  if (run.connection.value === 'run-lost') return { tone: 'danger' as const, label: 'Run interrupted', live: false };
  switch (run.status.value.state) {
    case 'alignment_waiting': return { tone: 'warning' as const, label: 'Waiting for alignment', live: true };
    case 'cancelling': return { tone: 'warning' as const, label: 'Interrupting', live: true };
    case 'cancelled': return { tone: 'warning' as const, label: 'Interrupted', live: false };
    case 'error': return { tone: 'danger' as const, label: 'Stopped with an error', live: false };
    case 'completed': return { tone: 'success' as const, label: 'Completed', live: false };
    default: return { tone: 'stage' as const, label: 'Running', live: true };
  }
});

const interrupting = computed(() => run.status.value.state === 'cancelling');
const interruptible = computed(() =>
  run.connection.value === 'online' && ['running', 'alignment_waiting', 'cancelling'].includes(run.status.value.state));

const confirmInterrupt = async () => {
  const ok = await showConfirm({
    message: 'Interrupt this run? It stops at the next safe point, and the steps already finished are kept.',
    confirmLabel: 'Interrupt',
    variant: 'danger',
  });
  if (ok) await store.cancelRun();
};
</script>

<template>
  <header class="flex flex-wrap items-center justify-between gap-4">
    <h1 class="type-title-1 text-fg1">Processing</h1>
    <div class="flex items-center gap-3">
      <BaseButton v-if="interruptible" variant="destructive" icon="x-mark" :disabled="interrupting" @click="confirmInterrupt">
        {{ interrupting ? 'Interrupting…' : 'Interrupt run' }}
      </BaseButton>
      <StatusPill :tone="pill.tone" :live="pill.live">{{ pill.label }}</StatusPill>
      <span class="type-headline nums text-fg1" :title="'Total elapsed time'">
        {{ run.status.value.run_started_at ? formatDuration(run.runSeconds.value) : '…' }}
      </span>
    </div>
  </header>
</template>
