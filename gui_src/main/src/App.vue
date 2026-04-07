<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useMainStore } from './store/main';
import SetupView from './views/SetupView.vue';
import ConfigView from './views/ConfigView.vue';
import RunningView from './views/RunningView.vue';
import CompleteView from './views/CompleteView.vue';

const store = useMainStore();
const isDark = ref(false);
const showSplash = ref(true);

const toggleDark = () => {
  isDark.value = !isDark.value;
  document.documentElement.classList.toggle('dark', isDark.value);
};

onMounted(async () => {
  isDark.value = window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.classList.toggle('dark', isDark.value);

  // Dismiss splash after 1.5 s; the 0.5 s CSS fade brings the total to 2 s
  setTimeout(() => { showSplash.value = false; }, 1500);

  await store.fetchSchema();
  await store.restoreState();
});
</script>

<template>
  <div class="h-screen overflow-hidden bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100">

    <!-- Intro splash -->
    <Transition name="splash-fade">
      <div
        v-if="showSplash"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-gray-50 dark:bg-gray-900"
      >
        <div class="flex gap-[0.05em]">
          <span
            v-for="(letter, i) in ['F','O','C','U','S']"
            :key="i"
            class="splash-letter text-6xl font-bold tracking-tight text-gray-900 dark:text-gray-100"
            :style="{ animationDelay: `${i * 0.18}s` }"
          >{{ letter }}</span>
        </div>
      </div>
    </Transition>

    <!-- Floating dark-mode toggle -->
    <button
      @click="toggleDark"
      class="fixed top-3 right-4 z-50 p-2 rounded-lg hover:bg-black/5 dark:hover:bg-white/10 transition-colors text-xl"
      :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
    >
      {{ isDark ? '\u2600\uFE0F' : '\uD83C\uDF19' }}
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

  </div>
</template>

<style scoped>
/* Each letter pops in from slightly below */
.splash-letter {
  opacity: 0;
  transform: translateY(8px);
  animation: letter-in 0.25s cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

@keyframes letter-in {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Whole overlay fades out */
.splash-fade-leave-active {
  transition: opacity 0.5s ease;
}
.splash-fade-leave-to {
  opacity: 0;
}
</style>