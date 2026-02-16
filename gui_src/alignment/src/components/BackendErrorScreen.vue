<template>
  <div class="fixed inset-0 z-50 flex flex-col items-center justify-center bg-red-900 text-white p-8 text-center">
    <h1 class="text-4xl font-bold mb-4">Backend Not Running</h1>
    <p class="text-xl mb-8">Unable to connect to the server. Please ensure the backend is running and try again.</p>
    <button @click="reload" class="px-6 py-3 bg-white text-red-900 font-bold rounded hover:bg-gray-200 transition-colors">
      Retry Connection
    </button>
  </div>
</template>

<script setup lang="ts">
const reload = () => {
  window.location.reload();
};
</script>
