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
  <div v-else-if="store.isFinished" class="flex flex-col items-center justify-center h-screen bg-green-900 text-white">
    <div class="bg-green-800 p-8 rounded-lg shadow-lg text-center">
        <h1 class="text-3xl font-bold mb-4">Alignment Completed</h1>
        <p class="text-xl">All samples have been successfully processed.</p>
        <p class="text-gray-300 mt-2">You may now close this window.</p>
    </div>
  </div>
  <MainLayout v-else />
</template>
