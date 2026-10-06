<script setup lang="ts">
/** Step 2 of Setup: confirm the discovered sample directories. */
import { useMainStore } from '../../store/main';
import { pluralize } from '../../utils/format';
import PromptCard from './PromptCard.vue';
import InsetWell from '../ui/InsetWell.vue';
import Banner from '../ui/Banner.vue';
import BaseButton from '../ui/BaseButton.vue';

const emit = defineEmits<{ confirm: []; back: [] }>();
const store = useMainStore();
</script>

<template>
  <PromptCard
    :title="pluralize(store.samples.length, 'sample') + ' found'"
    description="Verify the list matches your dataset before continuing."
    icon="check"
    tone="success"
  >
    <InsetWell v-if="store.samples.length > 0" padding="sm" class="max-h-[min(220px,30vh)] overflow-y-auto">
      <div
        v-for="(sample, idx) in store.samples"
        :key="sample"
        class="flex h-[30px] items-center gap-3 px-2.5"
      >
        <span class="type-mono-small text-fg3 w-7 shrink-0 text-right select-none">{{ idx + 1 }}</span>
        <span class="type-mono text-fg1 ellipsis">{{ sample }}</span>
      </div>
    </InsetWell>
    <Banner v-else tone="warning">
      No sample directories found. You can add samples manually in the config builder.
    </Banner>

    <template #actions>
      <BaseButton variant="primary" size="xl" class="flex-1" @click="emit('confirm')">Confirm and continue</BaseButton>
      <BaseButton variant="secondary" size="xl" @click="emit('back')">Back</BaseButton>
    </template>
  </PromptCard>
</template>
