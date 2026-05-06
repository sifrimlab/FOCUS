<script setup lang="ts">
import { computed } from 'vue';
import { useMainStore } from '../store/main';
import StageProgress from '../components/StageProgress.vue';

const store = useMainStore();
const s = computed(() => store.pipelineStatus);

const visibleStages = computed(() => {
  const cfg = store.config;
  const stages: { name: string; label: string }[] = [
    { name: 'preprocessing', label: 'Preprocessing' },
  ];
  if (cfg.perform_alignment && cfg.modalities.length >= 2) {
    stages.push({ name: 'alignment', label: 'Alignment' });
  }
  if (cfg.spatial_annotations !== null) {
    stages.push({ name: 'annotation_transfer', label: 'Annotation Transfer' });
  }
  if (cfg.perform_registration) {
    stages.push({ name: 'registration', label: 'Registration' });
    stages.push({ name: 'compiling', label: 'Compiling' });
  }
  return stages;
});

const openAlignmentTool = () => {
  window.open(`http://localhost:${s.value.alignment_port}`, '_blank');
};

const hasSampleProgress = computed(() => s.value.total_samples > 0);
const hasStepProgress   = computed(() => !!s.value.sub_step);
const hasStepDots       = computed(() => s.value.sub_step_total > 0 && s.value.sub_step_total <= 9);
const hasItemProgress   = computed(() => s.value.sub_step_items_total > 0);

const samplePct = computed(() =>
  hasSampleProgress.value
    ? Math.min(100, Math.round((s.value.current_sample_index / s.value.total_samples) * 100))
    : 0
);

const itemPct = computed(() =>
  hasItemProgress.value
    ? Math.min(100, Math.round((s.value.sub_step_progress / s.value.sub_step_items_total) * 100))
    : 0
);

const stepDescription = computed(() => {
  const step = s.value.sub_step;
  if (!step) return '';
  const match = step.match(/^\d+\/\d+\s*[-–]\s*(.*)/);
  return match ? match[1] : step;
});

const stepArray = computed(() =>
  s.value.sub_step_total > 0
    ? Array.from({ length: s.value.sub_step_total }, (_, i) => i + 1)
    : []
);

// Width of the progress line between step dots
const stepLinePct = computed(() => {
  const n = stepArray.value.length;
  const idx = s.value.sub_step_index;
  if (n <= 1 || idx <= 1) return 0;
  return Math.round(((idx - 1) / (n - 1)) * 100);
});
</script>

<template>
  <div class="h-full flex flex-col items-center justify-center px-4 py-6">
  <div class="w-full max-w-2xl flex flex-col gap-5">

    <!-- Stage progress -->
    <div>
      <StageProgress
        :current-stage="s.stage"
        :stages="visibleStages"
      />
    </div>

    <!-- Main status card -->
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow border border-gray-200 dark:border-gray-700 overflow-hidden">

      <!-- Card header: stage label + modality badge -->
      <div class="flex items-center justify-between px-5 py-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700">
        <span class="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
          {{ s.stage ? s.stage.replace(/_/g, ' ') : 'Initializing' }}
        </span>
        <span
          v-if="s.current_modality"
          class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300"
        >
          <span class="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          {{ s.current_modality }}
          <span v-if="s.total_modalities > 0" class="font-normal font-mono opacity-60" style="font-feature-settings: 'zero'">
            {{ s.current_modality_index }}/{{ s.total_modalities }}
          </span>
        </span>
      </div>

      <div class="p-5 space-y-5">

        <!-- Sample progress -->
        <div v-if="hasSampleProgress">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 shrink-0">
                Sample
              </span>
              <span class="font-medium font-mono text-gray-800 dark:text-gray-100 truncate" style="font-feature-settings: 'zero'">
                {{ s.current_sample }}
              </span>
            </div>
            <span class="text-xs font-mono text-gray-400 dark:text-gray-500 shrink-0 ml-3" style="font-feature-settings: 'zero'">
              {{ s.current_sample_index }} / {{ s.total_samples }}
            </span>
          </div>
          <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
            <div
              class="bg-indigo-500 h-2 rounded-full transition-all duration-500"
              :style="{ width: `${samplePct}%` }"
            />
          </div>
        </div>

        <!-- Step progress -->
        <div v-if="hasStepProgress">

          <!-- Step dots tracker (shown when ≤9 named steps) -->
          <div v-if="hasStepDots" class="relative flex items-center justify-between mb-4 px-3">
            <!-- Background track -->
            <div class="absolute inset-x-3 top-3 h-px bg-gray-200 dark:bg-gray-700" />
            <!-- Progress track -->
            <div
              class="absolute left-3 top-3 h-px bg-blue-400 dark:bg-blue-500 transition-all duration-500"
              :style="{ width: `calc(${stepLinePct}% * (100% - 1.5rem) / 100)` }"
            />
            <!-- Dots -->
            <div
              v-for="n in stepArray"
              :key="n"
              class="relative z-10 flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold ring-2 bg-white dark:bg-gray-800 transition-all duration-300"
              :class="{
                'ring-green-500 bg-green-500 text-white': n < s.sub_step_index,
                'ring-blue-600 bg-blue-600 !bg-blue-600 text-white scale-110': n === s.sub_step_index,
                'ring-gray-300 dark:ring-gray-600 text-gray-400': n > s.sub_step_index,
              }"
            >
              <svg v-if="n < s.sub_step_index" class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              <span v-else>{{ n }}</span>
            </div>
          </div>

          <!-- Step label + item count -->
          <div class="flex items-baseline justify-between mb-1.5">
            <p class="text-sm font-medium text-gray-700 dark:text-gray-200 truncate pr-2">
              {{ stepDescription || s.sub_step }}
            </p>
            <p v-if="hasItemProgress" class="text-xs text-gray-400 dark:text-gray-500 shrink-0 font-mono" style="font-feature-settings: 'zero'">
              {{ s.sub_step_progress }} / {{ s.sub_step_items_total }}
            </p>
          </div>

          <!-- Item progress bar (or pulsing indeterminate) -->
          <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5 overflow-hidden">
            <div
              v-if="hasItemProgress"
              class="bg-blue-500 h-1.5 rounded-full transition-all duration-300"
              :style="{ width: `${itemPct}%` }"
            />
            <div v-else class="bg-blue-400 h-1.5 rounded-full animate-pulse w-full" />
          </div>
        </div>

        <!-- Message log -->
        <div class="bg-gray-50 dark:bg-gray-900 rounded-lg px-3 py-2.5 text-xs font-mono text-gray-500 dark:text-gray-400 min-h-[36px] leading-relaxed">
          {{ s.message || 'Starting...' }}
        </div>

      </div>
    </div>

    <!-- Alignment waiting -->
    <div
      v-if="s.state === 'alignment_waiting'"
      class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-400 dark:border-yellow-700 rounded-lg p-6 text-center"
    >
      <p class="font-semibold mb-3">Manual alignment required</p>
      <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">
        The alignment tool will open in a new tab. Complete the alignment for each sample, then return here.
      </p>
      <button
        @click="openAlignmentTool"
        class="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-bold text-lg"
      >
        Open Alignment Tool
      </button>
    </div>

    <!-- Error state -->
    <div
      v-if="s.state === 'error'"
      class="bg-red-50 dark:bg-red-900/20 border border-red-400 dark:border-red-700 rounded-lg p-6"
    >
      <h3 class="font-semibold text-red-700 dark:text-red-400 mb-2">Pipeline Error</h3>
      <p class="text-sm text-red-600 dark:text-red-400 font-mono mb-4">{{ s.error }}</p>
      <button
        @click="store.goToConfig()"
        class="px-4 py-2 bg-gray-200 dark:bg-gray-700 rounded hover:bg-gray-300 dark:hover:bg-gray-600 font-medium"
      >
        Back to Configuration
      </button>
    </div>

  </div>
  </div>
</template>