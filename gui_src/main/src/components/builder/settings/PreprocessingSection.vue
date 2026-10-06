<script setup lang="ts">
/** Section 1: preprocessing parameters of the modality's (read-only) type. */
import { computed } from 'vue';
import { useMainStore } from '../../../store/main';
import { FORCE_KEY } from '../../../utils/params';
import SettingsSection from './SettingsSection.vue';
import ParamSettingsForm from '../../config/ParamSettingsForm.vue';

const props = defineProps<{ index: number }>();
const store = useMainStore();
const modality = computed(() => store.config.modalities[props.index]!);
</script>

<template>
  <SettingsSection
    :number="1"
    title="Preprocessing"
    :description="`Parameters for ${store.displayName(modality.type)} data. The type is set in the Modalities step.`"
  >
    <ParamSettingsForm
      :specs="store.schema?.processing_params[modality.type] ?? {}"
      :settings="modality.processing_settings"
      :exclude="[FORCE_KEY]"
      empty-text="nullable"
      @update="store.updateModality(index, { processing_settings: $event })"
    />
  </SettingsSection>
</template>
