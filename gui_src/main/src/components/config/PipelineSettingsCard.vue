<script setup lang="ts">
/** Reference modality, optional HuggingFace token, alignment/registration switches. */
import { useMainStore } from '../../store/main';
import GlassCard from '../ui/GlassCard.vue';
import CardHeader from '../ui/CardHeader.vue';
import FormRow from '../ui/FormRow.vue';
import SelectField from '../ui/SelectField.vue';
import TextField from '../ui/TextField.vue';
import ToggleSwitch from '../ui/ToggleSwitch.vue';

const store = useMainStore();

// Registration needs alignment: switching alignment off also switches registration off.
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
  <GlassCard class="flex flex-col gap-4">
    <CardHeader title="Pipeline settings" />

    <FormRow label="Reference modality">
      <SelectField
        class="w-56"
        :model-value="store.config.reference_modality"
        placeholder="Select reference…"
        aria-label="Reference modality"
        @update:model-value="store.setReferenceModality($event)"
      >
        <option v-for="name in store.modalityNames" :key="name" :value="name">{{ name }}</option>
      </SelectField>
    </FormRow>

    <FormRow v-if="store.needsHuggingfaceToken" label="HuggingFace token">
      <TextField
        v-model="store.config.huggingface_token"
        class="w-56"
        type="password"
        placeholder="hf_..."
        aria-label="HuggingFace token"
        @input="store.triggerAutoSave()"
      />
    </FormRow>

    <FormRow label="Perform alignment">
      <ToggleSwitch label="Perform alignment" :model-value="store.config.perform_alignment" @update:model-value="setAlignment" />
    </FormRow>

    <FormRow label="Perform registration" :disabled="!store.config.perform_alignment">
      <ToggleSwitch
        label="Perform registration"
        :model-value="store.config.perform_registration"
        :disabled="!store.config.perform_alignment"
        @update:model-value="setRegistration"
      />
    </FormRow>
  </GlassCard>
</template>
