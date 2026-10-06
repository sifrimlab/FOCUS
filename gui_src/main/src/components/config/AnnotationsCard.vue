<script setup lang="ts">
/** Optional spatial annotation loading: source modality and file type. */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import type { SpatialAnnotations } from '../../api/types';
import GlassCard from '../ui/GlassCard.vue';
import CardHeader from '../ui/CardHeader.vue';
import FormRow from '../ui/FormRow.vue';
import SelectField from '../ui/SelectField.vue';
import ToggleSwitch from '../ui/ToggleSwitch.vue';

const store = useMainStore();

const annotations = computed(() => store.config.spatial_annotations);
const fileTypes = computed(() => store.schema?.annotation_file_types ?? ['geojson']);

const setEnabled = (on: boolean) => {
  store.config.spatial_annotations = on
    ? ({
        modality_name: store.modalityNames[0] ?? '',
        file_type: store.schema?.annotation_file_types[0] ?? 'geojson',
      } as SpatialAnnotations)
    : null;
  store.triggerAutoSave();
};

const update = (changes: Partial<SpatialAnnotations>) => {
  if (!store.config.spatial_annotations) return;
  Object.assign(store.config.spatial_annotations, changes);
  store.triggerAutoSave();
};
</script>

<template>
  <GlassCard class="flex flex-col gap-4">
    <CardHeader title="Spatial annotations" />

    <FormRow label="Load spatial annotations">
      <ToggleSwitch label="Load spatial annotations" :model-value="annotations !== null" @update:model-value="setEnabled" />
    </FormRow>

    <template v-if="annotations">
      <FormRow label="Annotation modality">
        <SelectField
          class="w-56"
          :model-value="annotations.modality_name"
          placeholder="Select modality…"
          aria-label="Annotation modality"
          @update:model-value="update({ modality_name: $event })"
        >
          <option v-for="name in store.modalityNames" :key="name" :value="name">{{ name }}</option>
        </SelectField>
      </FormRow>

      <FormRow label="Annotation file type">
        <SelectField
          class="w-56"
          :model-value="annotations.file_type"
          aria-label="Annotation file type"
          @update:model-value="update({ file_type: $event })"
        >
          <option v-for="ft in fileTypes" :key="ft" :value="ft">{{ ft }}</option>
        </SelectField>
      </FormRow>

      <p class="type-footnote text-fg3">
        FOCUS expects one annotation file per sample in
        <code class="type-mono-small rounded-tiny bg-inset px-1">{sample_id}/{{ annotations.modality_name }}/</code>.
      </p>
    </template>
  </GlassCard>
</template>
