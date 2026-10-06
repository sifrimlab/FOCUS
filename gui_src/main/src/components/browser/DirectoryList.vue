<script setup lang="ts" generic="E extends DirectoryEntry">
/**
 * Presentational directory listing (DESIGN.md 8.10): an up button, the
 * current path, and rows for entries. Folder rows emit `open`, file rows
 * emit `pick`. State comes from useDirectoryBrowser; this file only renders.
 */
import AppIcon from '../ui/AppIcon.vue';
import IconButton from '../ui/IconButton.vue';
import InsetWell from '../ui/InsetWell.vue';
import type { DirectoryEntry } from '../../composables/useDirectoryBrowser';

defineProps<{
  path: string;
  parent: string | null;
  entries: E[];
  loading: boolean;
  error: string;
  emptyText: string;
  maxHeight: string;
}>();

const emit = defineEmits<{ up: []; open: [name: string]; pick: [name: string] }>();

const isDir = (entry: E) => entry.is_dir !== false;
</script>

<template>
  <InsetWell padding="none" class="overflow-hidden">
    <div class="flex items-center gap-2 px-2 py-1.5 border-b border-separator">
      <IconButton
        icon="arrow-up"
        label="Go up"
        size="sm"
        :disabled="parent === null || loading"
        @click="emit('up')"
      />
      <span class="type-mono-small text-fg3 ellipsis flex-1" :title="path">{{ path || '…' }}</span>
    </div>

    <div class="overflow-y-auto p-1" :style="{ maxHeight }">
      <p v-if="loading" class="type-callout text-fg3 py-6 text-center">Loading…</p>
      <p v-else-if="error" class="type-mono-small text-danger-fg px-3 py-3">{{ error }}</p>
      <p v-else-if="entries.length === 0" class="type-callout text-fg3 px-3 py-3">{{ emptyText }}</p>
      <template v-else>
        <button
          v-for="entry in entries"
          :key="entry.name"
          type="button"
          class="row type-mono flex w-full items-center gap-2.5 rounded-row px-2.5 text-left"
          :class="isDir(entry) ? 'text-fg1' : 'text-primary-fg'"
          @click="isDir(entry) ? emit('open', entry.name) : emit('pick', entry.name)"
        >
          <AppIcon :name="isDir(entry) ? 'folder' : 'document-text'" class="text-fg3" />
          <span class="ellipsis">{{ entry.name }}</span>
        </button>
      </template>
    </div>
  </InsetWell>
</template>

<style scoped>
.row {
  height: var(--control-md);
  transition: background-color var(--dur-fast) var(--ease-out);
}
.row:hover { background: var(--mat-inset-hover); }
</style>
