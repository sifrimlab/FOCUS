<script setup lang="ts">
import type { OutputFiles } from '../api/types';

defineProps<{ files: OutputFiles }>();

const SECTIONS: { key: keyof OutputFiles; label: string }[] = [
  { key: 'multimodal',    label: 'Multimodal Dataset' },
  { key: 'registration',  label: 'Registration Artifacts' },
  { key: 'annotations',   label: 'Annotated Artifacts' },
  { key: 'alignment',     label: 'Aligned Artifacts' },
  { key: 'preprocessing', label: 'Preprocessed Artifacts' },
];

function basename(path: string): string {
  return path.split('/').pop() ?? path;
}

function hasPerModality(section: NonNullable<OutputFiles[keyof OutputFiles]>): boolean {
  return Object.values(section.per_modality).some(arr => arr.length > 0);
}

function totalPerModality(section: NonNullable<OutputFiles[keyof OutputFiles]>): number {
  return Object.values(section.per_modality).reduce((sum, arr) => sum + arr.length, 0);
}
</script>

<template>
  <div v-if="Object.keys(files).length > 0" class="flex flex-col gap-3">
    <template v-for="sec in SECTIONS" :key="sec.key">
      <div
        v-if="files[sec.key] && (files[sec.key]!.merged.length > 0 || hasPerModality(files[sec.key]!))"
        class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden"
      >
        <!-- Section header -->
        <div class="px-4 py-2 bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-700">
          <span class="text-sm font-semibold text-gray-700 dark:text-gray-300">{{ sec.label }}</span>
        </div>

        <div class="px-4 py-3 flex flex-col gap-2">
          <!-- Merged outputs (always visible) -->
          <div v-if="files[sec.key]!.merged.length > 0">
            <p class="text-xs text-gray-500 dark:text-gray-400 mb-1 font-medium uppercase tracking-wide">Main output</p>
            <div
              v-for="f in files[sec.key]!.merged"
              :key="f"
              class="font-mono text-xs text-gray-800 dark:text-gray-200 py-1 px-2 bg-gray-50 dark:bg-gray-900/40 rounded"
            >
              {{ basename(f) }}
            </div>
          </div>

          <!-- Per-modality per-sample outputs (collapsible, grouped by modality) -->
          <details
            v-if="hasPerModality(files[sec.key]!)"
            class="text-xs"
          >
            <summary class="cursor-pointer text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 select-none py-1">
              Per-sample files ({{ totalPerModality(files[sec.key]!) }})
            </summary>
            <div class="mt-1 flex flex-col gap-2">
              <template v-for="(paths, modName) in files[sec.key]!.per_modality" :key="modName">
                <div v-if="paths.length > 0">
                  <p class="text-xs text-gray-400 dark:text-gray-500 font-medium mb-0.5 pl-2">{{ modName }}</p>
                  <div class="flex flex-col gap-0.5 pl-2 border-l-2 border-gray-200 dark:border-gray-600">
                    <div
                      v-for="f in paths"
                      :key="f"
                      class="font-mono text-xs text-gray-600 dark:text-gray-400 py-0.5"
                    >
                      {{ basename(f) }}
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </details>
        </div>
      </div>
    </template>
  </div>
</template>
