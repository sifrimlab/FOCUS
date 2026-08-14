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

    <!-- Top-right control strip: external links + dark-mode toggle -->
    <div class="fixed top-3 right-4 z-50 flex items-center gap-0.5">

      <!-- GitHub -->
      <a
        href="https://github.com/sifrimlab/FOCUS"
        target="_blank"
        rel="noopener noreferrer"
        title="GitHub repository"
        class="p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
      >
        <svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M12 2C6.477 2 2 6.484 2 12.021c0 4.428 2.865 8.185 6.839 9.504.5.092.682-.217.682-.482 0-.237-.009-.868-.013-1.703-2.782.605-3.369-1.342-3.369-1.342-.454-1.155-1.11-1.463-1.11-1.463-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.339-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0 1 12 6.844a9.59 9.59 0 0 1 2.504.337c1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482C19.138 20.203 22 16.447 22 12.021 22 6.484 17.523 2 12 2z"/>
        </svg>
      </a>

      <!-- Documentation / Wiki -->
      <a
        href="https://sifrimlab.org/FOCUS/"
        target="_blank"
        rel="noopener noreferrer"
        title="Documentation"
        class="p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/>
        </svg>
      </a>

      <!-- Scientific paper -->
      <a
        href="https://doi.org/10.64898/2026.08.04.742705"
        target="_blank"
        rel="noopener noreferrer"
        title="Scientific paper"
        class="p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4.26 10.147a60.436 60.436 0 00-.491 6.347A48.627 48.627 0 0112 20.904a48.627 48.627 0 018.232-4.41 60.46 60.46 0 00-.491-6.347m-15.482 0a50.57 50.57 0 00-2.658-.813A59.905 59.905 0 0112 3.493a59.902 59.902 0 0110.399 5.84c-.896.248-1.783.52-2.658.814m-15.482 0A50.697 50.697 0 0112 13.489a50.702 50.702 0 017.74-3.342M6.75 15a.75.75 0 100-1.5.75.75 0 000 1.5zm0 0v-3.675A55.378 55.378 0 0112 8.443m-7.007 11.55A5.981 5.981 0 006.75 15.75v-1.5"/>
        </svg>
      </a>

      <!-- Divider -->
      <span class="w-px h-4 bg-gray-300 dark:bg-gray-600 mx-1"></span>

      <!-- Dark-mode toggle -->
      <button
        @click="toggleDark"
        class="p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
        :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
      >
        <svg v-if="isDark" class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707m12.728 0l-.707-.707M6.343 6.343l-.707-.707M12 7a5 5 0 100 10A5 5 0 0012 7z"/>
        </svg>
        <svg v-else class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>
      </button>
    </div>

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
