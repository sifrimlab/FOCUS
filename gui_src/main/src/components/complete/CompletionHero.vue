<script setup lang="ts">
/** Success header of the Complete view (DESIGN.md 8.5): drawn check, title, summary. */
import GlassCard from '../ui/GlassCard.vue';
</script>

<template>
  <GlassCard hero padding="lg" class="hero flex flex-col items-center gap-3 text-center">
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" class="check" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.5" class="check__ring" />
      <path d="M9 12.75 11.25 15 15 9.75" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="check__tick" />
    </svg>
    <h1 class="type-title-1 text-fg1">Pipeline completed</h1>
    <p class="type-body text-fg2">All processing stages have finished successfully.</p>
  </GlassCard>
</template>

<style scoped>
.hero { background-image: linear-gradient(var(--success-soft), var(--success-soft)); }
.check { color: var(--success); }
.check__ring {
  stroke-dasharray: 57;
  stroke-dashoffset: 57;
  animation: draw var(--dur-slow) var(--ease-out) 120ms forwards;
}
.check__tick {
  stroke-dasharray: 10;
  stroke-dashoffset: 10;
  animation: draw var(--dur-base) var(--ease-out) 420ms forwards;
}
@keyframes draw { to { stroke-dashoffset: 0; } }

@media (prefers-reduced-motion: reduce) {
  .check__ring, .check__tick { animation: none; stroke-dashoffset: 0; }
}
</style>
