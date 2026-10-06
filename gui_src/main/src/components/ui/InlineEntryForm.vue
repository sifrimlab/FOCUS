<script setup lang="ts">
/**
 * Inline "name + Add + Cancel" row. Pair with the useInlineEntry composable:
 * bind its value with v-model, pass error/busy, wire confirm/cancel, and
 * hand its inputRef to `ref` so open() can focus the field.
 */
import { ref } from 'vue';
import TextField from './TextField.vue';
import BaseButton from './BaseButton.vue';

const model = defineModel<string>({ default: '' });

withDefaults(defineProps<{
  label: string;
  placeholder?: string;
  error?: string;
  busy?: boolean;
  confirmLabel?: string;
  busyLabel?: string;
}>(), { confirmLabel: 'Add', busyLabel: 'Adding…' });

const emit = defineEmits<{ confirm: []; cancel: [] }>();

const field = ref<InstanceType<typeof TextField> | null>(null);
defineExpose({ focus: () => field.value?.focus() });
</script>

<template>
  <div class="flex flex-col gap-1.5">
    <span class="type-footnote text-fg3">{{ label }}</span>
    <div class="flex items-center gap-2">
      <TextField
        ref="field"
        v-model="model"
        class="flex-1"
        :placeholder="placeholder"
        :aria-label="label"
        :invalid="!!error"
        :disabled="busy"
        @keyup.enter="emit('confirm')"
        @keyup.escape="emit('cancel')"
      />
      <BaseButton variant="tinted" :disabled="busy" @click="emit('confirm')">
        {{ busy ? busyLabel : confirmLabel }}
      </BaseButton>
      <BaseButton variant="secondary" :disabled="busy" @click="emit('cancel')">Cancel</BaseButton>
    </div>
    <p v-if="error" class="type-footnote text-danger-fg">{{ error }}</p>
  </div>
</template>
