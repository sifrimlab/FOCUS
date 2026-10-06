<script setup lang="ts">
/**
 * The status polls are failing. Polling goes on, so the screen resumes by
 * itself if the server answers again (network glitch, run still going).
 */
import { formatClock } from '../../utils/duration';
import Banner from '../ui/Banner.vue';
import StatusPill from '../ui/StatusPill.vue';

defineProps<{ lastContactAt: number | null }>();
</script>

<template>
  <Banner tone="warning" title="Connection to the FOCUS server lost" standalone>
    <p>
      <template v-if="lastContactAt">No response since {{ formatClock(lastContactAt / 1000) }}. </template>
      The run may still be in progress; the display resumes as soon as the server responds.
      If the server process stopped (for example, out of memory), restart it and reopen this page.
    </p>
    <template #actions>
      <StatusPill tone="warning" class="keep-live">Retrying…</StatusPill>
    </template>
  </Banner>
</template>
