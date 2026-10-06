<script setup lang="ts">
/** Step 3 of Setup (optional): load the existing focus_config.json or start fresh. */
import PromptCard from './PromptCard.vue';
import BaseButton from '../ui/BaseButton.vue';

const emit = defineEmits<{ load: []; fresh: [] }>();
</script>

<template>
  <PromptCard title="Existing configuration found" icon="exclamation-triangle" tone="warning">
    <p class="type-callout text-fg2">
      A <code class="type-mono-small rounded-tiny bg-inset px-1">focus_config.json</code> file already exists in this
      directory. Would you like to load it or start fresh?
    </p>

    <template #actions>
      <BaseButton variant="primary" size="xl" class="flex-1" @click="emit('load')">Load existing config</BaseButton>
      <BaseButton variant="secondary" size="xl" @click="emit('fresh')">Start fresh</BaseButton>
    </template>
  </PromptCard>
</template>
