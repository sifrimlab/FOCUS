<script setup lang="ts">
/** The run stopped (pipeline error or interrupted run) with a way back to the configuration. */
import Banner from '../ui/Banner.vue';
import BaseButton from '../ui/BaseButton.vue';

withDefaults(defineProps<{ title?: string; error?: string | null }>(), { title: 'Pipeline error', error: null });
const emit = defineEmits<{ back: [] }>();
</script>

<template>
  <Banner tone="danger" :title="title" standalone>
    <p v-if="error" class="type-mono text-danger-fg break-words">{{ error }}</p>
    <slot />
    <template #actions>
      <BaseButton size="lg" icon="arrow-left" @click="emit('back')">Back to configuration</BaseButton>
    </template>
  </Banner>
</template>
