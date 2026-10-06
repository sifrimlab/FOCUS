<script setup lang="ts">
/**
 * Alignment and registration switches. Registration needs alignment, and
 * both need at least two modalities; while disabled, stored values are kept.
 * Used on the Modalities step and on Review.
 */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import FormRow from '../ui/FormRow.vue';
import ToggleSwitch from '../ui/ToggleSwitch.vue';

const store = useMainStore();

const enoughModalities = computed(() => store.config.modalities.length >= 2);
const hint = computed(() => (enoughModalities.value ? undefined : 'Needs at least two modalities'));

const setAlignment = (on: boolean) => {
  store.config.perform_alignment = on;
  if (!on) store.config.perform_registration = false;
  store.triggerAutoSave();
};

const setRegistration = (on: boolean) => {
  store.config.perform_registration = on;
  store.triggerAutoSave();
};
</script>

<template>
  <div class="flex flex-col gap-3">
    <FormRow label="Alignment" :hint="hint ?? 'Map the reference onto each other modality in the alignment tool.'" :disabled="!enoughModalities">
      <ToggleSwitch
        label="Perform alignment"
        :model-value="store.config.perform_alignment"
        :disabled="!enoughModalities"
        @update:model-value="setAlignment"
      />
    </FormRow>
    <FormRow
      label="Registration"
      :hint="hint ?? (store.config.perform_alignment ? 'Transfer features onto the reference spots.' : 'Needs alignment')"
      :disabled="!enoughModalities || !store.config.perform_alignment"
    >
      <ToggleSwitch
        label="Perform registration"
        :model-value="store.config.perform_registration"
        :disabled="!enoughModalities || !store.config.perform_alignment"
        @update:model-value="setRegistration"
      />
    </FormRow>
  </div>
</template>
