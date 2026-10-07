<script setup lang="ts">
/**
 * Single radio control for choosing one row among many (e.g. the reference
 * modality). Rendered as role="radio"; group semantics come from the parent
 * (role="radiogroup").
 */
defineProps<{ checked: boolean; label: string }>();
const emit = defineEmits<{ select: [] }>();
</script>

<template>
  <button
    type="button"
    role="radio"
    class="radio"
    :aria-checked="checked"
    :aria-label="label"
    :title="label"
    @click="emit('select')"
  >
    <span class="radio__dot" />
  </button>
</template>

<style scoped>
.radio {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-full);
  box-shadow: inset 0 0 0 1.5px var(--separator-strong);
  transition: background-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
}
.radio:hover { box-shadow: inset 0 0 0 1.5px var(--fg4); }
.radio[aria-checked="true"] { background: var(--primary-fill); box-shadow: none; }
.radio__dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--fg-inverse);
  transform: scale(0);
  transition: transform var(--dur-base) var(--ease-spring-snappy);
}
.radio[aria-checked="true"] .radio__dot { transform: scale(1); }
</style>
