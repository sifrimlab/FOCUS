<script setup lang="ts">
/**
 * The status polls are failing. Polling goes on, so the screen resumes by
 * itself if the server answers again (network glitch, run still going).
 */
import Banner from '../ui/Banner.vue';
import StatusPill from '../ui/StatusPill.vue';
</script>

<template>
  <Banner tone="warning" title="Connection to the FOCUS server lost" standalone>
    <p>Check that the FOCUS server is still running and reachable from this machine.</p>
    <template #actions>
      <StatusPill tone="warning" class="keep-live">Retrying…</StatusPill>
    </template>
  </Banner>
</template>
