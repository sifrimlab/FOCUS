<template>
  <div class="flex h-screen w-full overflow-hidden bg-gray-100 dark:bg-gray-800">
    <div class="w-[80%] h-full relative overflow-hidden border-r border-gray-300 dark:border-gray-700 flex items-center justify-center bg-white dark:bg-black">
      <!-- Canvas Container -->
      <div class="relative w-full h-full">
        <ReferenceCanvas />
        <TargetCanvas />
      </div>
    </div>
    <div class="w-[20%] h-full overflow-y-auto bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
      <ControlPanel />
    </div>
  </div>
</template>

<script setup lang="ts">
import ReferenceCanvas from '../components/ReferenceCanvas.vue';
import TargetCanvas from '../components/TargetCanvas.vue';
import ControlPanel from '../components/ControlPanel.vue';
</script>
