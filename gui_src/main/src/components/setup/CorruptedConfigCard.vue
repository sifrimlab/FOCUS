<script setup lang="ts">
/** Shown when the existing config cannot be read: repair it or overwrite it. */
import PromptCard from './PromptCard.vue';
import Banner from '../ui/Banner.vue';
import BaseButton from '../ui/BaseButton.vue';

defineProps<{ errors: string[] }>();
const emit = defineEmits<{ overwrite: []; back: [] }>();
</script>

<template>
  <PromptCard title="Configuration file is corrupted" icon="exclamation-circle" tone="danger">
    <Banner tone="danger">
      <ul class="list-disc list-inside space-y-1">
        <li v-for="(err, i) in errors" :key="i">{{ err }}</li>
      </ul>
    </Banner>
    <p class="type-callout text-fg3">
      You can go back and repair the file manually, or proceed with a fresh configuration (the corrupted file will be
      overwritten).
    </p>

    <template #actions>
      <BaseButton variant="destructive-filled" shape="pill" size="xl" class="flex-1" @click="emit('overwrite')">
        Proceed fresh (overwrite)
      </BaseButton>
      <BaseButton variant="secondary" size="xl" @click="emit('back')">Go back</BaseButton>
    </template>
  </PromptCard>
</template>
