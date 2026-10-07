<script setup lang="ts">
/**
 * Launch splash: animated stack mark and wordmark. Branding, kept as
 * designed (geometry, timing, curve). Hides itself after HOLD_MS.
 */
import { onMounted, ref } from 'vue';
import BrandWordmark from '@focus/ui/components/shell/BrandWordmark.vue';

// Last animation starts at 700ms and runs 400ms, so it completes at 1100ms.
// Hold 1000ms, dismiss at 2100ms; the 500ms fade brings the total to ~2600ms.
const HOLD_MS = 2100;

const visible = ref(true);
onMounted(() => { window.setTimeout(() => { visible.value = false; }, HOLD_MS); });
</script>

<template>
  <Transition name="splash-fade">
    <div v-if="visible" class="splash fixed inset-0 z-splash flex items-center justify-center">
      <div class="flex flex-col items-center gap-6">
        <!-- Same geometry as logo-mark.svg -->
        <svg width="64" height="64" viewBox="0 0 64 64" fill="none" class="mark overflow-visible" aria-hidden="true">
          <rect class="splash-back" x="14" y="22" width="32" height="32" rx="3" stroke="currentColor" stroke-width="2" stroke-opacity="0.35" />
          <rect class="splash-mid" x="18" y="18" width="32" height="32" rx="3" stroke="currentColor" stroke-width="2" stroke-opacity="0.6" />
          <rect class="splash-front" x="22" y="14" width="32" height="32" rx="3" stroke="currentColor" stroke-width="2.5" />
          <circle class="splash-dot-1" cx="30" cy="38" r="2" fill="currentColor" fill-opacity="0.4" />
          <circle class="splash-dot-2" cx="34" cy="34" r="2" fill="currentColor" fill-opacity="0.65" />
          <circle class="splash-dot-accent" cx="38" cy="30" r="2.5" />
        </svg>
        <BrandWordmark class="splash-word" />
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.splash { background: var(--backdrop-base); }
.mark { color: var(--brand-ink); }
.splash-dot-accent { fill: var(--brand-accent); }

.splash-back   { opacity: 0; animation: splash-slide-back 500ms cubic-bezier(0.22, 1, 0.36, 1) forwards; }
.splash-mid    { opacity: 0; animation: splash-slide-mid 500ms 80ms cubic-bezier(0.22, 1, 0.36, 1) forwards; }
.splash-front  { opacity: 0; animation: splash-fade-in 400ms 160ms cubic-bezier(0.22, 1, 0.36, 1) forwards; }
.splash-dot-1  { opacity: 0; animation: splash-fade-in 300ms 320ms cubic-bezier(0.22, 1, 0.36, 1) forwards; }
.splash-dot-2  { opacity: 0; animation: splash-fade-in 300ms 400ms cubic-bezier(0.22, 1, 0.36, 1) forwards; }
.splash-dot-accent { opacity: 0; animation: splash-fade-in 300ms 500ms cubic-bezier(0.22, 1, 0.36, 1) forwards; }
.splash-word   { opacity: 0; animation: splash-word-in 400ms 700ms cubic-bezier(0.22, 1, 0.36, 1) forwards; }

@keyframes splash-slide-back { from { opacity: 0; transform: translate(-14px, 14px); } to { opacity: 1; transform: translate(0, 0); } }
@keyframes splash-slide-mid  { from { opacity: 0; transform: translate(-7px, 7px); }   to { opacity: 1; transform: translate(0, 0); } }
@keyframes splash-fade-in    { from { opacity: 0; } to { opacity: 1; } }
@keyframes splash-word-in    { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

.splash-fade-leave-active { transition: opacity 0.5s ease; }
.splash-fade-leave-to { opacity: 0; }
</style>
