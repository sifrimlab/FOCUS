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
  <div v-else-if="store.error" class="fixed inset-0 z-50 flex flex-col items-center justify-center bg-red-900 text-white p-8 text-center">
    <h1 class="text-4xl font-bold mb-4">Alignment Error</h1>
    <p class="text-xl mb-4">The alignment process encountered an error and could not continue.</p>
    <p class="text-base font-mono bg-red-800 rounded p-4 mb-8 max-w-2xl break-all">{{ store.error }}</p>
    <p class="text-gray-300">Please check the server logs for details, then close this window and restart the pipeline.</p>
  </div>
  <LoadingScreen v-else-if="store.isLoading" />
  <div v-else-if="store.isFinished" class="flex flex-col items-center justify-center h-screen bg-green-900 text-white">
    <div class="bg-green-800 p-8 rounded-lg shadow-lg text-center">
        <h1 class="text-3xl font-bold mb-4">Alignment Completed</h1>
        <p class="text-xl">All samples have been successfully processed.</p>
        <p class="text-gray-300 mt-2">You may now close this window.</p>
    </div>
  </div>
  <MainLayout v-else />
</template>
