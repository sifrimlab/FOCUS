<script setup lang="ts">
/** Global confirm dialog driven by useDialog (DESIGN.md 8.9). Esc cancels, Enter confirms. */
import { onMounted, onUnmounted } from 'vue';
import { useDialog } from '../composables/useDialog';
import BaseButton from './ui/BaseButton.vue';

const { state, handleConfirm, handleCancel } = useDialog();

const onKeydown = (e: KeyboardEvent) => {
  if (!state.visible) return;
  if (e.key === 'Escape') handleCancel();
  if (e.key === 'Enter') handleConfirm();
};

onMounted(() => window.addEventListener('keydown', onKeydown));
onUnmounted(() => window.removeEventListener('keydown', onKeydown));
</script>

<template>
  <Transition name="fade">
    <div v-if="state.visible" class="scrim fixed inset-0 z-[200]" aria-hidden="true" />
  </Transition>
  <Transition name="pop">
    <div
      v-if="state.visible"
      class="pointer-events-none fixed inset-0 z-[201] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-message"
    >
      <div class="material-overlay rounded-dialog pointer-events-auto flex w-full max-w-sm flex-col gap-5 p-6">
        <p id="confirm-dialog-message" class="type-body text-fg1">{{ state.message }}</p>
        <div class="flex justify-end gap-2">
          <BaseButton size="lg" @click="handleCancel">{{ state.cancelLabel }}</BaseButton>
          <BaseButton
            size="lg"
            shape="rounded"
            :variant="state.variant === 'danger' ? 'destructive-filled' : 'primary'"
            @click="handleConfirm"
          >{{ state.confirmLabel }}</BaseButton>
        </div>
      </div>
    </div>
  </Transition>
</template>
