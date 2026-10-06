<script setup lang="ts">
/** Load a focus_config.json from disk by drag and drop or file chooser. */
import { ref } from 'vue';
import { useMainStore } from '../../store/main';
import GlassCard from '../ui/GlassCard.vue';
import CardHeader from '../ui/CardHeader.vue';
import DropZone from '../ui/DropZone.vue';

const store = useMainStore();
const error = ref('');
const loaded = ref(false);

const load = async (file: File | null) => {
  error.value = '';
  loaded.value = false;
  if (!file || !file.name.endsWith('.json')) {
    error.value = 'Please drop a .json file.';
    return;
  }
  const ok = await store.loadConfigFromFile(await file.text());
  if (ok) {
    loaded.value = true;
    store.goToConfig();
  } else {
    error.value = store.validationErrors.join('; ');
  }
};
</script>

<template>
  <GlassCard class="flex flex-col gap-4">
    <CardHeader title="Load a config file" />
    <DropZone accept=".json" @file="load">
      Drag and drop a <strong class="text-fg2">.json</strong> config file here, or click to browse
    </DropZone>
    <p v-if="error" class="type-footnote text-danger-fg">{{ error }}</p>
    <p v-if="loaded" class="type-footnote text-success-fg">Configuration loaded successfully.</p>
  </GlassCard>
</template>
