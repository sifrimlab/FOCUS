<script setup lang="ts">
/** App shell: backdrop, chrome, splash, the current view, and the global dialog. */
import { onMounted, type Component } from 'vue';
import { useMainStore } from './store/main';
import { useAmbientState } from './composables/useAmbientState';
import AmbientBackdrop from '@focus/ui/components/shell/AmbientBackdrop.vue';
import ChromeCluster from '@focus/ui/components/shell/ChromeCluster.vue';
import SplashScreen from './components/shell/SplashScreen.vue';
import ConfirmDialog from './components/ConfirmDialog.vue';
import SetupView from './views/SetupView.vue';
import ConfigView from './views/ConfigView.vue';
import RunningView from './views/RunningView.vue';
import CompleteView from './views/CompleteView.vue';

const VIEWS: Record<string, Component> = {
  setup: SetupView,
  config: ConfigView,
  running: RunningView,
  complete: CompleteView,
};

const store = useMainStore();
const ambient = useAmbientState();

onMounted(async () => {
  await store.fetchSchema();
  await store.restoreState();
});
</script>

<template>
  <AmbientBackdrop :state="ambient" />
  <SplashScreen />
  <ChromeCluster />

  <main class="h-screen overflow-y-auto">
    <Transition name="view" mode="out-in">
      <component :is="VIEWS[store.currentView]" :key="store.currentView" />
    </Transition>
  </main>

  <ConfirmDialog />
</template>
