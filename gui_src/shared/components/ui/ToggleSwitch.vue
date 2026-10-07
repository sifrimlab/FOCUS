<script setup lang="ts">
/**
 * macOS-style switch (DESIGN.md 8.3). Exposed as role="switch".
 * Emits update:modelValue; use v-model or @update:model-value.
 */
const model = defineModel<boolean>({ default: false });

withDefaults(defineProps<{
  label: string;
  tone?: 'primary' | 'warning';
  disabled?: boolean;
}>(), { tone: 'primary' });
</script>

<template>
  <button
    type="button"
    role="switch"
    class="switch"
    :class="`switch--${tone}`"
    :aria-checked="model"
    :aria-label="label"
    :disabled="disabled"
    @click="model = !model"
  >
    <span class="switch__knob" />
  </button>
</template>

<style scoped>
.switch {
  position: relative;
  flex-shrink: 0;
  width: 38px;
  height: 22px;
  border-radius: var(--radius-full);
  background: var(--separator-strong);
  transition: background-color var(--dur-fast) var(--ease-out);
}
.switch--primary[aria-checked="true"] { background: var(--primary); }
.switch--warning[aria-checked="true"] { background: var(--warning); }
.switch:disabled { opacity: 0.4; cursor: not-allowed; }

.switch__knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: var(--radius-full);
  background: var(--fg-inverse);
  box-shadow: var(--elev-1), var(--mat-hairline);
  transition:
    transform var(--dur-base) var(--ease-spring-snappy),
    width var(--dur-fast) var(--ease-out);
}
.switch[aria-checked="true"] .switch__knob { transform: translateX(16px); }

/* iOS-style stretch while pressed. */
.switch:active:not(:disabled) .switch__knob { width: 21px; }
.switch[aria-checked="true"]:active:not(:disabled) .switch__knob { transform: translateX(13px); }
</style>
