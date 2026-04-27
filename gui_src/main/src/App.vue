<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useMainStore } from './store/main';
import SetupView from './views/SetupView.vue';
import ConfigView from './views/ConfigView.vue';
import RunningView from './views/RunningView.vue';
import CompleteView from './views/CompleteView.vue';
import ConfirmDialog from './components/ConfirmDialog.vue';

const store = useMainStore();
const isDark = ref(document.documentElement.classList.contains('dark'));
const showSplash = ref(true);

const toggleDark = () => {
  isDark.value = !isDark.value;
  document.documentElement.classList.toggle('dark', isDark.value);
  localStorage.setItem('focus-theme-override', '1');
};

onMounted(async () => {
  // Last animation starts at 700ms and runs 400ms → completes at 1100ms.
  // Hold 1000ms → dismiss at 2100ms; the 500ms CSS fade brings the total to ~2600ms.
  setTimeout(() => { showSplash.value = false; }, 2100);

  await store.fetchSchema();
  await store.restoreState();
});
</script>

<template>
  <div class="h-screen overflow-hidden bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100">

    <!-- Stack-mark splash -->
    <Transition name="splash-fade">
      <div
        v-if="showSplash"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-gray-50 dark:bg-gray-900"
      >
        <div class="flex flex-col items-center gap-6">
          <!-- Animated Stack mark (same geometry as logo-mark.svg) -->
          <svg
            width="64" height="64" viewBox="0 0 64 64" fill="none"
            class="text-slate-900 dark:text-slate-100 overflow-visible"
            aria-hidden="true"
          >
            <!-- Back layer: slides in from translate(-14px, 14px) -->
            <rect class="splash-back" x="14" y="22" width="32" height="32" rx="3"
              stroke="currentColor" stroke-width="2" stroke-opacity="0.35" />
            <!-- Mid layer: slides in from translate(-7px, 7px), 80ms delay -->
            <rect class="splash-mid" x="18" y="18" width="32" height="32" rx="3"
              stroke="currentColor" stroke-width="2" stroke-opacity="0.6" />
            <!-- Front layer: fades in, 160ms delay -->
            <rect class="splash-front" x="22" y="14" width="32" height="32" rx="3"
              stroke="currentColor" stroke-width="2.5" />
            <!-- Registration dots -->
            <circle class="splash-dot-1" cx="30" cy="38" r="2"
              fill="currentColor" fill-opacity="0.4" />
            <circle class="splash-dot-2" cx="34" cy="34" r="2"
              fill="currentColor" fill-opacity="0.65" />
            <!-- Accent dot: blue-600 light / blue-400 dark -->
            <circle
              class="splash-dot-accent"
              cx="38" cy="30" r="2.5"
              :style="{ fill: isDark ? '#60a5fa' : '#2563eb' }"
            />
          </svg>
          <!-- FOCUS wordmark -->
          <span
            class="splash-word text-5xl font-bold text-slate-900 dark:text-slate-100"
            style="letter-spacing: -0.02em; line-height: 1;"
          >FOCUS</span>
        </div>
      </div>
    </Transition>

    <!-- Floating dark-mode toggle -->
    <button
      @click="toggleDark"
      class="fixed top-3 right-4 z-50 p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/10 transition-colors text-xl"
      :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
    >
      {{ isDark ? '☀️' : '🌙' }}
    </button>

    <!-- Views -->
    <div v-if="store.currentView === 'config'" class="h-full overflow-y-auto">
      <ConfigView />
    </div>
    <template v-else>
      <SetupView   v-if="store.currentView === 'setup'" />
      <RunningView v-else-if="store.currentView === 'running'" />
      <CompleteView v-else-if="store.currentView === 'complete'" />
    </template>

    <!-- Global confirm dialog -->
    <ConfirmDialog />

  </div>
</template>

<style scoped>
/* ── Splash layer animations ────────────────────────────────── */
.splash-back {
  opacity: 0;
  animation: splash-slide-back 500ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
}
@keyframes splash-slide-back {
  from { opacity: 0; transform: translate(-14px, 14px); }
  to   { opacity: 1; transform: translate(0, 0); }
}

.splash-mid {
  opacity: 0;
  animation: splash-slide-mid 500ms 80ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
}
@keyframes splash-slide-mid {
  from { opacity: 0; transform: translate(-7px, 7px); }
  to   { opacity: 1; transform: translate(0, 0); }
}

.splash-front {
  opacity: 0;
  animation: splash-fade-in 400ms 160ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

.splash-dot-1 {
  opacity: 0;
  animation: splash-fade-in 300ms 320ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

.splash-dot-2 {
  opacity: 0;
  animation: splash-fade-in 300ms 400ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

.splash-dot-accent {
  opacity: 0;
  animation: splash-fade-in 300ms 500ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

.splash-word {
  opacity: 0;
  animation: splash-word-in 400ms 700ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

@keyframes splash-fade-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}

@keyframes splash-word-in {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ── Overlay fade-out ───────────────────────────────────────── */
.splash-fade-leave-active {
  transition: opacity 0.5s ease;
}
.splash-fade-leave-to {
  opacity: 0;
}
</style>
