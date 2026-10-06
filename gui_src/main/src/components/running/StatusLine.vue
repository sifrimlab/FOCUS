<script setup lang="ts">
/** Latest pipeline message (DESIGN.md 8.8): one mono line that cross-fades on change. */
defineProps<{ message: string }>();
</script>

<template>
  <div class="material-inset rounded-well relative flex min-h-[36px] items-center gap-2.5 px-3 py-2" aria-live="polite">
    <span class="dot" aria-hidden="true" />
    <div class="relative min-w-0 flex-1">
      <Transition name="swap">
        <p :key="message" class="type-mono text-fg2 break-words">{{ message }}</p>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--primary);
}
</style>
