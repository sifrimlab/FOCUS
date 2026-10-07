<script setup lang="ts">
/**
 * Text, number or password input (DESIGN.md 8.2).
 * `class` styles the outer box (use it for width); every other attribute
 * and listener goes to the native <input>, so @keyup.enter, @change,
 * placeholder, step, etc. work as on a plain input.
 */
import { ref } from 'vue';
import FieldShell from './FieldShell.vue';
import AppIcon from './AppIcon.vue';
import type { IconName } from '../../icons/paths';

defineOptions({ inheritAttrs: false });

const model = defineModel<string | number | null>();

withDefaults(defineProps<{
  type?: 'text' | 'number' | 'password';
  mono?: boolean;
  icon?: IconName;
  invalid?: boolean;
  disabled?: boolean;
  size?: 'md' | 'lg';
}>(), { type: 'text', size: 'md' });

const input = ref<HTMLInputElement | null>(null);

defineExpose({
  focus: () => input.value?.focus(),
  select: () => input.value?.select(),
});
</script>

<template>
  <FieldShell :class="$attrs.class" :invalid="invalid" :disabled="disabled" :size="size">
    <template #prefix>
      <AppIcon v-if="icon" :name="icon" class="text-fg3" />
      <slot name="prefix" />
    </template>
    <input
      ref="input"
      v-bind="{ ...$attrs, class: undefined }"
      v-model="model"
      :type="type"
      :disabled="disabled"
      :aria-invalid="invalid || undefined"
      :class="mono ? 'type-mono' : type === 'number' ? 'type-body nums' : 'type-body'"
    />
    <template #suffix><slot name="suffix" /></template>
  </FieldShell>
</template>
