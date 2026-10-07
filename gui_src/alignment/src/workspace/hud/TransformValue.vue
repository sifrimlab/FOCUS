<script setup lang="ts">
/** One read-only transform value in the collapsed transform island; pressing it opens its editor. */
defineProps<{ label: string; value: string; unit?: string }>();
defineEmits<{ open: [] }>();
</script>

<template>
  <button type="button" class="chip flex items-baseline gap-2 rounded-full px-3" :aria-label="`Edit ${label.toLowerCase()}`" @click="$emit('open')">
    <span class="type-footnote text-fg2">{{ label }}</span>
    <span class="type-mono nums text-fg1">{{ value }}<span v-if="unit" class="type-footnote text-fg3 ml-0.5">{{ unit }}</span></span>
  </button>
</template>

<style scoped>
.chip {
  height: var(--control-md);
  align-items: center;
  transition: background-color var(--dur-fast) var(--ease-out);
}
.chip:hover { background: var(--mat-inset-fill); }
</style>
