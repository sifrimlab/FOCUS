<script setup lang="ts">
import { ref } from 'vue';
import { useMainStore } from '../store/main';
import { api } from '../api/client';

const props = defineProps<{
  value: unknown;
  nullable?: boolean;
}>();

const emit = defineEmits<{
  'update:value': [val: string | null];
}>();

const store = useMainStore();
const showBrowser = ref(false);
const browserPath = ref('');
const browserParent = ref<string | null>(null);
const browserEntries = ref<{ name: string; is_dir: boolean }[]>([]);
const browserLoading = ref(false);
const browserError = ref('');

const openBrowser = async () => {
  showBrowser.value = true;
  const startPath = store.config.dataset_path || (props.value as string) || '';
  await browseTo(startPath);
};

const browseTo = async (path: string) => {
  browserLoading.value = true;
  browserError.value = '';
  try {
    const result = await api.browseFiles(path);
    browserPath.value = result.path;
    browserParent.value = result.parent;
    browserEntries.value = result.entries;
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : 'Could not browse';
    browserError.value = msg;
  } finally {
    browserLoading.value = false;
  }
};

const selectFile = (name: string) => {
  emit('update:value', browserPath.value + '/' + name);
  showBrowser.value = false;
};

const displayValue = (): string => {
  const v = props.value as string | null;
  if (!v) return '';
  return v.split('/').pop() || v;
};
</script>

<template>
  <div class="relative">
    <!-- Display + browse button (inset) -->
    <div
      class="flex items-center w-48 border rounded text-sm bg-white dark:bg-gray-700 border-gray-300 dark:border-gray-600 overflow-hidden"
      :title="(value as string) || ''"
    >
      <span
        class="flex-1 px-2 py-1.5 font-mono truncate min-w-0 select-none"
        :class="value ? 'text-gray-700 dark:text-gray-200' : 'text-gray-400 dark:text-gray-500 italic'"
      >
        {{ displayValue() || 'optional' }}
      </span>
      <!-- Separator -->
      <span class="w-px self-stretch bg-gray-200 dark:bg-gray-600 shrink-0"></span>
      <!-- Inset browse button -->
      <button
        @click="openBrowser"
        class="shrink-0 px-2 self-stretch flex items-center bg-white dark:bg-gray-700 hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors"
        title="Browse files"
      >
        <svg class="w-3.5 h-3.5 text-gray-400 dark:text-gray-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
        </svg>
      </button>
    </div>

    <!-- Inline browser panel -->
    <div
      v-if="showBrowser"
      class="absolute right-0 top-full mt-1 z-50 w-80 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-gray-200 dark:border-gray-700 overflow-hidden"
    >
      <!-- Header: current path + up button -->
      <div class="flex items-center gap-2 px-3 py-2 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700">
        <button
          @click="browseTo(browserParent!)"
          :disabled="browserParent === null || browserLoading"
          class="shrink-0 p-1 rounded transition-colors disabled:opacity-30"
          :class="browserParent !== null ? 'hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300' : 'text-gray-300 dark:text-gray-600'"
          title="Go up"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M5 10l7-7 7 7M12 3v18" />
          </svg>
        </button>
        <span class="flex-1 text-xs font-mono text-gray-500 dark:text-gray-400 truncate" :title="browserPath">
          {{ browserPath || '…' }}
        </span>
      </div>

      <!-- Entry list -->
      <div class="overflow-y-auto" style="max-height: 200px">
        <div v-if="browserLoading" class="flex items-center justify-center py-6 text-gray-400 text-xs">
          Loading…
        </div>
        <div v-else-if="browserError" class="px-3 py-3 text-xs text-red-500 font-mono">{{ browserError }}</div>
        <div v-else-if="browserEntries.length === 0" class="px-3 py-3 text-xs text-gray-400 italic">
          Empty directory
        </div>
        <button
          v-for="entry in browserEntries"
          :key="entry.name"
          @click="entry.is_dir ? browseTo(browserPath + '/' + entry.name) : selectFile(entry.name)"
          class="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left transition-colors"
          :class="entry.is_dir ? 'hover:bg-gray-50 dark:hover:bg-gray-700/60' : 'hover:bg-blue-50 dark:hover:bg-blue-900/20'"
        >
          <!-- Folder icon -->
          <svg v-if="entry.is_dir" class="w-3.5 h-3.5 text-blue-400 shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path d="M2 6a2 2 0 012-2h4l2 2h6a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
          </svg>
          <!-- File icon -->
          <svg v-else class="w-3.5 h-3.5 text-gray-400 shrink-0" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span
            class="font-mono truncate"
            :class="entry.is_dir ? 'text-gray-700 dark:text-gray-200' : 'text-blue-700 dark:text-blue-300'"
          >
            {{ entry.name }}
          </span>
        </button>
      </div>

      <!-- Footer -->
      <div class="flex justify-end px-3 py-2 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
        <button
          @click="showBrowser = false"
          class="px-3 py-1.5 text-xs bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg transition-colors font-medium"
        >
          Cancel
        </button>
      </div>
    </div>
  </div>
</template>