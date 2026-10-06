<script setup lang="ts">
/** View title (Title 1) with an optional subtitle slot. One per view. */
defineProps<{ title: string }>();
</script>

<template>
  <header class="flex flex-col gap-1">
    <h1 class="type-title-1 text-fg1">{{ title }}</h1>
    <div v-if="$slots.default" class="type-callout text-fg3"><slot /></div>
  </header>
</template>
