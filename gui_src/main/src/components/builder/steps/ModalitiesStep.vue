<script setup lang="ts">
/** Step 2: list the modalities (name and type), choose the reference, choose the pipeline scope. */
import { useMainStore } from '../../../store/main';
import StepFrame from '../StepFrame.vue';
import GlassCard from '@focus/ui/components/ui/GlassCard.vue';
import CardHeader from '@focus/ui/components/ui/CardHeader.vue';
import EmptyState from '@focus/ui/components/ui/EmptyState.vue';
import ModalityRow from '../modalities/ModalityRow.vue';
import ModalityAddRow from '../modalities/ModalityAddRow.vue';
import PipelineScope from '../PipelineScope.vue';

const store = useMainStore();
</script>

<template>
  <StepFrame
    title="Modalities"
    description="Add each modality in the dataset with its type, and choose the reference that the others are aligned to. Detailed settings come in the next step."
  >
    <GlassCard class="flex flex-col">
      <div class="modality-columns type-footnote text-fg3 grid gap-3 pb-2" aria-hidden="true">
        <span class="text-center">Reference</span>
        <span>Name</span>
        <span>Type</span>
        <span />
      </div>
      <div role="radiogroup" aria-label="Reference modality" class="flex flex-col divide-y divide-separator border-y border-separator">
        <TransitionGroup name="list">
          <ModalityRow v-for="(m, i) in store.config.modalities" :key="m.name + i" :index="i" />
        </TransitionGroup>
        <EmptyState v-if="store.config.modalities.length === 0" icon="plus" class="my-3">
          No modalities yet. Add the first one below.
        </EmptyState>
      </div>
      <ModalityAddRow />
    </GlassCard>

    <GlassCard class="flex flex-col gap-4">
      <CardHeader title="Pipeline steps" />
      <PipelineScope />
    </GlassCard>
  </StepFrame>
</template>

