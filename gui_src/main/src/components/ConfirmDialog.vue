<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import { useDialog } from '../composables/useDialog';

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
  <Transition name="dialog-fade">
    <div
      v-if="state.visible"
      class="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      aria-modal="true"
      role="dialog"
    >
      <div class="w-full max-w-sm mx-4 bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <!-- Body -->
        <div class="px-6 pt-6 pb-5">
          <p class="text-sm text-gray-800 dark:text-gray-100 leading-relaxed">{{ state.message }}</p>
        </div>

        <!-- Footer -->
        <div class="flex gap-2 px-6 pb-5 justify-end">
          <button
            @click="handleCancel"
            class="px-4 py-2 text-sm font-medium rounded-xl bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 transition-colors"
          >
            {{ state.cancelLabel }}
          </button>
          <button
            @click="handleConfirm"
            class="px-4 py-2 text-sm font-semibold rounded-xl text-white transition-colors"
            :class="state.variant === 'danger'
              ? 'bg-red-600 hover:bg-red-700'
              : 'bg-blue-600 hover:bg-blue-700'"
          >
            {{ state.confirmLabel }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.dialog-fade-enter-active,
.dialog-fade-leave-active {
  transition: opacity 0.15s ease;
}
.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;
}
</style>
