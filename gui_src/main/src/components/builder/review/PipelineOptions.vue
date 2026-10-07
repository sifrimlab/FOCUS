<script setup lang="ts">
/** Global run options on Review: pipeline scope, spatial annotations, HuggingFace token. */
import { computed } from 'vue';
import { useMainStore } from '../../../store/main';
import type { SpatialAnnotations } from '../../../api/types';
import PipelineScope from '../PipelineScope.vue';
import FormRow from '@focus/ui/components/ui/FormRow.vue';
import SelectField from '@focus/ui/components/ui/SelectField.vue';
import TextField from '@focus/ui/components/ui/TextField.vue';
import ToggleSwitch from '@focus/ui/components/ui/ToggleSwitch.vue';

const store = useMainStore();

const annotations = computed(() => store.config.spatial_annotations);
const fileTypes = computed(() => store.schema?.annotation_file_types ?? ['geojson']);

const setAnnotations = (on: boolean) => {
  store.config.spatial_annotations = on
    ? ({
        modality_name: store.modalityNames[0] ?? '',
        file_type: store.schema?.annotation_file_types[0] ?? 'geojson',
      } as SpatialAnnotations)
    : null;
  store.triggerAutoSave();
};

const updateAnnotations = (changes: Partial<SpatialAnnotations>) => {
  if (!store.config.spatial_annotations) return;
  Object.assign(store.config.spatial_annotations, changes);
  store.triggerAutoSave();
};
</script>

<template>
  <div class="flex flex-col gap-3">
    <PipelineScope />

    <div class="border-t border-separator" />

    <FormRow label="Spatial annotations" hint="Transfer per-sample annotation files to the outputs.">
      <ToggleSwitch label="Load spatial annotations" :model-value="annotations !== null" @update:model-value="setAnnotations" />
    </FormRow>
    <template v-if="annotations">
      <FormRow label="Annotation modality">
        <SelectField
          class="w-56"
          :model-value="annotations.modality_name"
          placeholder="Select modality…"
          aria-label="Annotation modality"
          @update:model-value="updateAnnotations({ modality_name: $event })"
        >
          <option v-for="name in store.modalityNames" :key="name" :value="name">{{ name }}</option>
        </SelectField>
      </FormRow>
      <FormRow label="Annotation file type">
        <SelectField
          class="w-56"
          :model-value="annotations.file_type"
          aria-label="Annotation file type"
          @update:model-value="updateAnnotations({ file_type: $event })"
        >
          <option v-for="ft in fileTypes" :key="ft" :value="ft">{{ ft }}</option>
        </SelectField>
      </FormRow>
      <p class="type-footnote text-fg3">
        FOCUS expects one annotation file per sample in
        <code class="type-mono-small rounded-tiny bg-inset px-1">{sample_id}/{{ annotations.modality_name }}/</code>.
      </p>
    </template>

    <template v-if="store.needsHuggingfaceToken">
      <div class="border-t border-separator" />
      <FormRow label="HuggingFace token" hint="Needed by the feature-extraction registration method.">
        <TextField
          v-model="store.config.huggingface_token"
          class="w-56"
          type="password"
          placeholder="hf_..."
          aria-label="HuggingFace token"
          @input="store.triggerAutoSave()"
        />
      </FormRow>
    </template>
  </div>
</template>
