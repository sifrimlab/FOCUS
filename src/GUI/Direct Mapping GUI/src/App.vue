<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { useMainStore } from './store/main';
import MainLayout from './layouts/MainLayout.vue';
import WarningScreen from './components/WarningScreen.vue';
import LoadingScreen from './components/LoadingScreen.vue';
import BackendErrorScreen from './components/BackendErrorScreen.vue';

const store = useMainStore();
const isSmallScreen = ref(window.innerWidth < 720);

const checkScreenSize = () => {
  isSmallScreen.value = window.innerWidth < 720;
};

onMounted(() => {
  window.addEventListener('resize', checkScreenSize);
  store.fetchNextSample();
});

onUnmounted(() => {
  window.removeEventListener('resize', checkScreenSize);
});
</script>

<template>
  <WarningScreen v-if="isSmallScreen" />
  <BackendErrorScreen v-else-if="store.isBackendDown" />
  <LoadingScreen v-else-if="store.isLoading" />
  <div v-else-if="store.isFinished" class="flex items-center justify-center h-screen bg-gray-900 text-white">
    <h1 class="text-2xl">All the samples have been aligned. You may close the window.</h1>
  </div>
  <MainLayout v-else />
</template>
