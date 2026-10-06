<script setup lang="ts">
/**
 * Native <select> in the shared field box with a custom chevron
 * (DESIGN.md 8.2). The native menu is kept for accessibility.
 * Pass <option> elements in the default slot. `class` styles the box;
 * other attributes and listeners go to the <select>.
 */
import FieldShell from './FieldShell.vue';
import AppIcon from './AppIcon.vue';

defineOptions({ inheritAttrs: false });

const model = defineModel<string>({ required: true });

defineProps<{
  placeholder?: string;
  disabled?: boolean;
}>();
</script>

<template>
  <FieldShell :class="$attrs.class" :disabled="disabled">
    <select
      v-bind="{ ...$attrs, class: undefined }"
      v-model="model"
      :disabled="disabled"
      class="type-body appearance-none pr-6"
    >
      <option v-if="placeholder" value="" disabled>{{ placeholder }}</option>
      <slot />
    </select>
    <template #suffix>
      <AppIcon name="chevron-up-down" class="text-fg3 pointer-events-none absolute right-2.5" />
    </template>
  </FieldShell>
</template>
