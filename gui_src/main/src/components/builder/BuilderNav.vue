<script setup lang="ts">
/**
 * Floating bottom bar of the builder. Back on the left; on the right the
 * reason the step is blocked (if any) and the forward action: Next, "Done,
 * back to review" in edit mode, or Start processing on Review.
 */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import { useBuilderStore, BUILDER_STEPS } from '../../store/builder';
import ActionBar from '../ui/ActionBar.vue';
import BaseButton from '../ui/BaseButton.vue';

const store = useMainStore();
const builder = useBuilderStore();

const nextLabel = computed(() => {
  if (builder.step === 'settings') {
    const next = store.config.modalities[builder.activeModality + 1];
    if (next) return `Next: ${next.name}`;
  }
  const following = BUILDER_STEPS[builder.index + 1];
  return following ? `Next: ${following.label}` : 'Next';
});

const canGoBack = computed(() => builder.index > 0 || builder.activeModality > 0);
</script>

<template>
  <ActionBar>
    <template #start>
      <BaseButton size="lg" icon="arrow-left" :disabled="!canGoBack" @click="builder.back()">Back</BaseButton>
    </template>
    <template #end>
      <span v-if="builder.blockReason" class="type-footnote text-fg3 hidden sm:inline" role="status">
        {{ builder.blockReason }}
      </span>
      <BaseButton
        v-if="builder.step === 'review'"
        variant="success"
        size="xl"
        icon="play"
        :disabled="store.isLoading || !!builder.blockReason"
        @click="store.startPipeline()"
      >{{ store.isLoading ? 'Validating…' : 'Start processing' }}</BaseButton>
      <BaseButton
        v-else-if="builder.editingFromReview"
        variant="primary"
        size="xl"
        icon="check"
        :disabled="!!builder.blockReason"
        @click="builder.returnToReview()"
      >Done, back to review</BaseButton>
      <BaseButton
        v-else
        variant="primary"
        size="xl"
        :disabled="!!builder.blockReason"
        @click="builder.next()"
      >{{ nextLabel }}</BaseButton>
    </template>
  </ActionBar>
</template>
