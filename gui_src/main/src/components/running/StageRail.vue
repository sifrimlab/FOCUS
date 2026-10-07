<script setup lang="ts">
/** Left column: every planned stage with its status and duration; doubles as the stage color legend. */
import { injectRunProgress } from '../../composables/useRunProgress';
import GlassCard from '@focus/ui/components/ui/GlassCard.vue';
import StageRailItem from './StageRailItem.vue';

const run = injectRunProgress();
</script>

<template>
  <GlassCard as="nav" padding="none" class="p-3" aria-label="Pipeline stages">
    <h2 class="type-footnote text-fg3 px-2 pt-1 pb-2">Stages</h2>
    <ol class="flex flex-col gap-1">
      <StageRailItem v-for="(stage, i) in run.stages.value" :key="stage.id" :stage="stage" :index="i" />
    </ol>
  </GlassCard>
</template>
