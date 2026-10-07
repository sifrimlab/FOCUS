<script setup lang="ts">
/**
 * Shown when the existing config cannot be read. Back returns to the
 * existing-config prompt (repair the file, then load again); Start fresh
 * opens the import step.
 */
import PromptCard from './PromptCard.vue';
import Banner from '@focus/ui/components/ui/Banner.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';

defineProps<{ errors: string[] }>();
const emit = defineEmits<{ fresh: []; back: [] }>();
</script>

<template>
  <PromptCard title="Configuration file is corrupted" icon="exclamation-circle" tone="danger" back @back="emit('back')">
    <Banner tone="danger">
      <ul class="list-disc list-inside space-y-1">
        <li v-for="(err, i) in errors" :key="i">{{ err }}</li>
      </ul>
    </Banner>
    <p class="type-callout text-fg3">
      You can repair the file manually and load it again, or start fresh. The corrupted file is replaced only once you choose a
      new configuration in the next step.
    </p>

    <template #actions>
      <BaseButton variant="primary" size="xl" block @click="emit('fresh')">Start fresh</BaseButton>
    </template>
  </PromptCard>
</template>
