<script setup lang="ts">
/**
 * Setup step shown when there is no config to resume: upload a
 * focus_config.json (preview, then confirm) or start from an empty config.
 * Emits `done` once the store holds the chosen config.
 */
import { computed, ref } from 'vue';
import { useMainStore } from '../../store/main';
import { readConfigFile, summarizeConfig } from '../../utils/configFile';
import PromptCard from './PromptCard.vue';
import ConfigPreview from './ConfigPreview.vue';
import DropZone from '../ui/DropZone.vue';
import Banner from '../ui/Banner.vue';
import BaseButton from '../ui/BaseButton.vue';

const emit = defineEmits<{ done: []; back: [] }>();
const store = useMainStore();

const fileName = ref('');
const data = ref<Record<string, unknown> | null>(null);
const errors = ref<string[]>([]);
const busy = ref(false);

const summary = computed(() => (data.value ? summarizeConfig(data.value) : null));

const clear = () => {
  fileName.value = '';
  data.value = null;
  errors.value = [];
};

const pick = async (file: File | null) => {
  clear();
  if (!file) return;
  const result = await readConfigFile(file);
  fileName.value = result.name;
  if ('error' in result) errors.value = [result.error];
  else data.value = result.data;
};

/** Run a store action with the busy state; emit done when it succeeds. */
const finish = async (action: () => Promise<string[] | null>) => {
  busy.value = true;
  errors.value = [];
  try {
    const failed = await action();
    if (failed) errors.value = failed;
    else emit('done');
  } finally {
    busy.value = false;
  }
};

const useFile = () => finish(async () => {
  const result = await store.importConfig(data.value!);
  return result.ok ? null : result.errors;
});

const startEmpty = () => finish(async () => {
  await store.startEmptyConfig();
  return null;
});
</script>

<template>
  <PromptCard
    title="How do you want to start?"
    description="Load an existing configuration file, or build a new configuration from scratch."
    icon="document-arrow-up"
  >
    <Transition name="fade" mode="out-in">
      <ConfigPreview v-if="fileName" :file-name="fileName" :summary="summary" @clear="clear" />
      <DropZone v-else accept=".json,application/json" @file="pick">
        Drag and drop a <strong class="text-fg2">focus_config.json</strong> here, or click to browse
      </DropZone>
    </Transition>

    <Transition name="fade">
      <Banner v-if="errors.length" tone="danger" title="This configuration cannot be used">
        <ul class="list-disc pl-4 space-y-0.5">
          <li v-for="(err, i) in errors" :key="i">{{ err }}</li>
        </ul>
      </Banner>
    </Transition>

    <template #actions>
      <div class="flex w-full flex-col gap-3">
        <div class="flex gap-3">
          <BaseButton
            variant="primary"
            size="xl"
            class="flex-1"
            :disabled="!data || busy"
            @click="useFile"
          >{{ busy && data ? 'Loading…' : 'Use this config' }}</BaseButton>
          <BaseButton variant="secondary" size="xl" :disabled="busy" @click="startEmpty">
            Start with an empty config
          </BaseButton>
        </div>
        <BaseButton variant="plain" icon="arrow-left" class="self-start" :disabled="busy" @click="emit('back')">
          Back
        </BaseButton>
      </div>
    </template>
  </PromptCard>
</template>
