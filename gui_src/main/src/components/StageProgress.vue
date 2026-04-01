<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  currentStageIndex: number;
  totalStages: number;
}>();

const stages = [
  { index: 1, label: 'Preprocessing' },
  { index: 2, label: 'Alignment' },
  { index: 3, label: 'Registration' },
  { index: 4, label: 'Compiling' },
];

const stageStatus = computed(() => {
  return stages.map(s => ({
    ...s,
    completed: s.index < props.currentStageIndex,
    active: s.index === props.currentStageIndex,
    pending: s.index > props.currentStageIndex,
  }));
});
</script>

<template>
  <div class="flex items-center justify-between w-full max-w-2xl mx-auto">
    <template v-for="(stage, i) in stageStatus" :key="stage.index">
      <!-- Stage circle + label -->
      <div class="flex flex-col items-center">
        <div
          class="w-10 h-10 rounded-full flex items-center justify-center font-bold text-sm border-2 transition-colors"
          :class="{
            'bg-green-600 border-green-600 text-white': stage.completed,
            'bg-blue-600 border-blue-600 text-white animate-pulse': stage.active,
            'bg-gray-200 dark:bg-gray-700 border-gray-300 dark:border-gray-600 text-gray-500': stage.pending,
          }"
        >
          <span v-if="stage.completed">&#10003;</span>
          <span v-else>{{ stage.index }}</span>
        </div>
        <span class="text-xs mt-1 font-medium" :class="{ 'text-blue-600 dark:text-blue-400': stage.active }">
          {{ stage.label }}
        </span>
      </div>

      <!-- Connector line -->
      <div
        v-if="i < stageStatus.length - 1"
        class="flex-1 h-0.5 mx-2 mb-5"
        :class="stageStatus[i + 1]?.completed || stageStatus[i + 1]?.active ? 'bg-blue-400' : 'bg-gray-300 dark:bg-gray-600'"
      ></div>
    </template>
  </div>
</template>
