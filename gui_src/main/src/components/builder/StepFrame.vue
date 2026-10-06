<script setup lang="ts">
/**
 * Shared frame of every builder step: Title 1, one-line description, an
 * optional toolbar slot aligned with the title, then the step content.
 * Keeps the header position identical across steps.
 */
defineProps<{ title: string; description: string }>();
</script>

<template>
  <section class="flex flex-col gap-8">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div class="flex min-w-0 flex-col gap-1">
        <h1 class="type-title-1 text-fg1">{{ title }}</h1>
        <p class="type-callout text-fg3 max-w-[60ch]">{{ description }}</p>
      </div>
      <slot name="toolbar" />
    </header>
    <div class="flex flex-col gap-6">
      <slot />
    </div>
  </section>
</template>
