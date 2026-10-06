<script setup lang="ts">
/**
 * Step 1 of Setup: type or browse to the dataset root, then continue.
 * Emits `continue` once the store has the path and its samples.
 */
import { onMounted, ref } from 'vue';
import { useMainStore } from '../../store/main';
import { api } from '../../api/client';
import { useDirectoryBrowser } from '../../composables/useDirectoryBrowser';
import PromptCard from './PromptCard.vue';
import TextField from '../ui/TextField.vue';
import BaseButton from '../ui/BaseButton.vue';
import DirectoryList from '../browser/DirectoryList.vue';

const LAST_PATH_KEY = 'focus_last_dataset_path';

const emit = defineEmits<{ continue: [] }>();

const store = useMainStore();
const browser = useDirectoryBrowser(api.browse);

const pathInput = ref(store.config.dataset_path || localStorage.getItem(LAST_PATH_KEY) || '');
const pathError = ref('');

/** Navigate the browser; when the user clicked, also mirror the path into the field. */
const navigate = async (target: string | null, updateInput: boolean) => {
  if (target === null) return;
  const resolved = await browser.browseTo(target);
  if (resolved !== null && updateInput) pathInput.value = resolved;
};

const onPathInput = () => {
  if (pathInput.value.endsWith('/')) navigate(pathInput.value, false);
};

const submit = async () => {
  pathError.value = '';
  const path = pathInput.value.trim();
  if (!path) {
    pathError.value = 'Please enter a dataset path.';
    return;
  }
  localStorage.setItem(LAST_PATH_KEY, path);
  await store.setDatasetPath(path);
  emit('continue');
};

onMounted(() => navigate(pathInput.value, false));
</script>

<template>
  <PromptCard
    title="Dataset path"
    description="Absolute path to the root directory containing your sample subdirectories."
  >
    <div class="flex flex-col gap-3">
      <div class="flex flex-col gap-1.5">
        <TextField
          v-model="pathInput"
          mono
          size="lg"
          icon="folder"
          placeholder="/path/to/dataset"
          aria-label="Dataset path"
          :invalid="!!pathError"
          @input="onPathInput"
          @keyup.enter="submit"
        />
        <p v-if="pathError" class="type-footnote text-danger-fg">{{ pathError }}</p>
      </div>

      <DirectoryList
        :path="browser.path.value"
        :parent="browser.parent.value"
        :entries="browser.entries.value"
        :loading="browser.loading.value"
        :error="browser.error.value"
        empty-text="No subdirectories"
        max-height="min(240px, 35vh)"
        @up="navigate(browser.parent.value, true)"
        @open="navigate(browser.childPath($event), true)"
      />
    </div>

    <template #actions>
      <BaseButton variant="primary" size="xl" block @click="submit">Continue</BaseButton>
    </template>
  </PromptCard>
</template>
