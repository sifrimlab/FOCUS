<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useMainStore } from './store/main';
import SetupView from './views/SetupView.vue';
import ConfigView from './views/ConfigView.vue';
import RunningView from './views/RunningView.vue';
import CompleteView from './views/CompleteView.vue';

const store = useMainStore();

const isDark = ref(false);

const toggleDark = () => {
  isDark.value = !isDark.value;
  document.documentElement.classList.toggle('dark', isDark.value);
};

onMounted(async () => {
  isDark.value = window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.classList.toggle('dark', isDark.value);
  await store.fetchSchema();
  await store.restoreState();
});
</script>

<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- Navigation bar -->
    <nav class="sticky top-0 z-50 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-3 flex items-center justify-between">
      <span class="text-lg font-bold tracking-wide">FOCUS</span>
      <button
        @click="toggleDark"
        class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-xl"
        :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
      >
        {{ isDark ? '\u2600\uFE0F' : '\uD83C\uDF19' }}
      </button>
    </nav>

    <!-- View content -->
    <SetupView v-if="store.currentView === 'setup'" />
    <ConfigView v-else-if="store.currentView === 'config'" />
    <RunningView v-else-if="store.currentView === 'running'" />
    <CompleteView v-else-if="store.currentView === 'complete'" />
  </div>
</template>
