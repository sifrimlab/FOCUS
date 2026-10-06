<script setup lang="ts">
/** Shown when the existing config cannot be read: repair it, or start fresh in the import step. */
import PromptCard from './PromptCard.vue';
import Banner from '../ui/Banner.vue';
import BaseButton from '../ui/BaseButton.vue';

defineProps<{ errors: string[] }>();
const emit = defineEmits<{ fresh: []; back: [] }>();
</script>

<template>
  <PromptCard title="Configuration file is corrupted" icon="exclamation-circle" tone="danger">
    <Banner tone="danger">
      <ul class="list-disc list-inside space-y-1">
        <li v-for="(err, i) in errors" :key="i">{{ err }}</li>
      </ul>
    </Banner>
    <p class="type-callout text-fg3">
      You can go back and repair the file manually, or start fresh. The corrupted file is replaced only once you choose a
      new configuration in the next step.
    </p>

    <template #actions>
      <BaseButton variant="primary" size="xl" class="flex-1" @click="emit('fresh')">Start fresh</BaseButton>
      <BaseButton variant="secondary" size="xl" @click="emit('back')">Go back</BaseButton>
    </template>
  </PromptCard>
</template>
