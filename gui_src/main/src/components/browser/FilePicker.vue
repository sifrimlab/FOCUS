<script setup lang="ts">
/**
 * Path parameter control: shows the selected file name and opens a file
 * browser popover (DESIGN.md 8.10). Emits the full selected path.
 */
import { computed, ref } from 'vue';
import { useMainStore } from '../../store/main';
import { api } from '../../api/client';
import { useDirectoryBrowser } from '../../composables/useDirectoryBrowser';
import { useAnchoredPopover } from '@focus/ui/composables/useAnchoredPopover';
import { basename } from '../../utils/format';
import FieldShell from '@focus/ui/components/ui/FieldShell.vue';
import IconButton from '@focus/ui/components/ui/IconButton.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import DirectoryList from './DirectoryList.vue';

const props = defineProps<{ value: unknown }>();
const emit = defineEmits<{ 'update:value': [value: string | null] }>();

const store = useMainStore();
const browser = useDirectoryBrowser(api.browseFiles);
const anchor = ref<HTMLElement | null>(null);
const popover = useAnchoredPopover(anchor);

const fullPath = computed(() => (props.value as string | null) || '');
const label = computed(() => (fullPath.value ? basename(fullPath.value) : ''));

const openBrowser = async () => {
  popover.open();
  await browser.browseTo(store.config.dataset_path || fullPath.value);
};

const pick = (name: string) => {
  emit('update:value', browser.childPath(name));
  popover.close();
};
</script>

<template>
  <div ref="anchor" class="w-56">
    <FieldShell :title="fullPath">
      <span class="type-mono flex-1 ellipsis" :class="label ? 'text-fg1' : 'text-fg4'">{{ label || 'optional' }}</span>
      <template #suffix>
        <IconButton icon="folder-open" label="Browse files" size="sm" class="-mr-1.5" @click="openBrowser" />
      </template>
    </FieldShell>

    <Teleport to="body">
      <Transition name="popover">
        <div
          v-if="popover.isOpen.value"
          class="material-popover rounded-card fixed z-popover w-80 origin-top-right p-2"
          :style="popover.style.value"
        >
          <DirectoryList
            :path="browser.path.value"
            :parent="browser.parent.value"
            :entries="browser.entries.value"
            :loading="browser.loading.value"
            :error="browser.error.value"
            empty-text="Empty directory"
            max-height="200px"
            @up="browser.goUp()"
            @open="browser.browseTo(browser.childPath($event))"
            @pick="pick"
          />
          <div class="flex justify-end pt-2">
            <BaseButton @click="popover.close()">Cancel</BaseButton>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>
