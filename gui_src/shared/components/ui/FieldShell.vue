<script setup lang="ts">
/**
 * Visual box shared by every form control (DESIGN.md 8.2): inset fill,
 * control radius and height, focus ring on focus-within, error outline.
 * TextField, SelectField and FilePicker compose it; none restyle it.
 */
withDefaults(defineProps<{
  invalid?: boolean;
  disabled?: boolean;
  size?: 'md' | 'lg';
}>(), { size: 'md' });
</script>

<template>
  <div
    class="field"
    :class="{ 'field--invalid': invalid, 'field--disabled': disabled, 'field--lg': size === 'lg' }"
  >
    <slot name="prefix" />
    <slot />
    <slot name="suffix" />
  </div>
</template>

<style scoped>
.field {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  height: var(--control-md);
  padding: 0 10px;
  border-radius: var(--radius-md);
  background: var(--mat-inset-fill);
  color: var(--fg1);
  transition: background-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
}
.field--lg { height: var(--control-lg); padding: 0 12px; }
.field:hover { background: var(--mat-inset-hover); }

/* Inside an inset well the field lifts to the content material so it stays distinct. */
.material-inset .field { background: var(--mat-content-fill); box-shadow: var(--mat-hairline); }
.field:focus-within {
  background: var(--mat-content-fill);
  box-shadow: inset 0 0 0 1px var(--primary), var(--focus-ring);
}
.field--invalid,
.field--invalid:focus-within { box-shadow: inset 0 0 0 1px var(--danger); }
.field--disabled { opacity: 0.4; pointer-events: none; }

/* Native controls inside the shell are bare; the shell draws everything. */
.field :deep(input),
.field :deep(select) {
  flex: 1;
  min-width: 0;
  height: 100%;
  background: transparent;
  color: inherit;
  outline: none;
}
.field :deep(input::placeholder) { color: var(--fg4); }
</style>
