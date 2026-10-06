<script setup lang="ts">
/** Fatal pipeline error with a way back to the configuration. */
import Banner from '../ui/Banner.vue';
import BaseButton from '../ui/BaseButton.vue';

defineProps<{ error: string | null }>();
const emit = defineEmits<{ back: [] }>();
</script>

<template>
  <Banner tone="danger" title="Pipeline error" standalone>
    <p class="type-mono text-danger-fg break-words">{{ error }}</p>
    <template #actions>
      <BaseButton size="lg" icon="arrow-left" @click="emit('back')">Back to configuration</BaseButton>
    </template>
  </Banner>
</template>
