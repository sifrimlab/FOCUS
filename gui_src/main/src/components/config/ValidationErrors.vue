<script setup lang="ts">
/** Server-side validation errors that block the pipeline start. */
import Banner from '@focus/ui/components/ui/Banner.vue';

defineProps<{ errors: string[] }>();
</script>

<template>
  <Banner tone="danger" title="Validation errors" standalone>
    <ul class="list-disc pl-5">
      <li v-for="(err, i) in errors" :key="i">{{ err }}</li>
    </ul>
  </Banner>
</template>
