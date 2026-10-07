<script setup lang="ts">
/**
 * Drag-and-drop target that also opens the file chooser on click or
 * Enter/Space (DESIGN.md 8.13). Emits the first selected file.
 */
import { ref } from 'vue';
import AppIcon from './AppIcon.vue';
import type { IconName } from '../../icons/paths';

withDefaults(defineProps<{
  accept?: string;
  icon?: IconName;
}>(), { icon: 'document-arrow-up' });

const emit = defineEmits<{ file: [file: File | null] }>();

const dragging = ref(false);
const input = ref<HTMLInputElement | null>(null);

const onDrop = (e: DragEvent) => {
  dragging.value = false;
  emit('file', e.dataTransfer?.files[0] ?? null);
};

const onPick = (e: Event) => {
  const target = e.target as HTMLInputElement;
  const file = target.files?.[0];
  if (file) emit('file', file);
  target.value = '';
};
</script>

<template>
  <div
    role="button"
    tabindex="0"
    class="dropzone rounded-well flex flex-col items-center justify-center gap-2 px-6 py-7 text-center"
    :class="{ 'dropzone--active': dragging }"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
    @click="input?.click()"
    @keydown.enter.prevent="input?.click()"
    @keydown.space.prevent="input?.click()"
  >
    <AppIcon :name="icon" :size="20" class="text-fg3" />
    <div class="type-callout text-fg3"><slot /></div>
    <input ref="input" type="file" :accept="accept" class="hidden" @change="onPick" />
  </div>
</template>

<style scoped>
.dropzone {
  background: var(--mat-inset-fill);
  outline: 1.5px dashed var(--separator-strong);
  outline-offset: -1.5px;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    outline-color var(--dur-fast) var(--ease-out),
    transform var(--dur-base) var(--ease-spring);
}
.dropzone:hover { background: var(--mat-inset-hover); }
.dropzone--active {
  background: var(--primary-soft);
  outline-color: var(--primary);
  transform: scale(1.01);
}
.dropzone:focus-visible { outline: 3px solid var(--focus-color); outline-offset: 1px; }
</style>
