<script setup lang="ts">
/** Step 4: summary of the whole configuration, global options, force flags, then Start processing. */
import { computed } from 'vue';
import { useMainStore } from '../../../store/main';
import { useBuilderStore } from '../../../store/builder';
import StepFrame from '../StepFrame.vue';
import ReviewSection from '../review/ReviewSection.vue';
import ReviewModalities from '../review/ReviewModalities.vue';
import PipelineOptions from '../review/PipelineOptions.vue';
import ForceMatrix from '../review/ForceMatrix.vue';
import ValidationErrors from '../../config/ValidationErrors.vue';

const store = useMainStore();
const builder = useBuilderStore();

const excluded = computed(() => store.samples.filter(id => store.config.ignore_samples.includes(id)));
const includedCount = computed(() => store.samples.length - excluded.value.length);
</script>

<template>
  <StepFrame
    title="Review"
    description="Check the configuration, adjust the run options, then start processing. Select any part to change it."
  >
    <Transition name="fade">
      <ValidationErrors v-if="store.validationErrors.length > 0" :errors="store.validationErrors" />
    </Transition>

    <ReviewSection title="Samples" edit-label="Edit" @edit="builder.goTo('samples')">
      <template #meta>{{ includedCount }} of {{ store.samples.length }} included</template>
      <p class="type-callout text-fg2">
        <template v-if="excluded.length === 0">All discovered samples take part in the run.</template>
        <template v-else>
          Excluded:
          <span class="type-mono-small text-fg3">{{ excluded.join(', ') }}</span>
        </template>
      </p>
    </ReviewSection>

    <ReviewSection title="Modalities" edit-label="Edit list" @edit="builder.goTo('modalities')">
      <template #meta>{{ store.config.modalities.length }}</template>
      <ReviewModalities />
    </ReviewSection>

    <ReviewSection title="Run options">
      <PipelineOptions />
    </ReviewSection>

    <ReviewSection title="Force recompute">
      <ForceMatrix />
    </ReviewSection>
  </StepFrame>
</template>
