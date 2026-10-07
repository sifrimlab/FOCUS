<script setup lang="ts">
/** Section 3: registration method compatible with the modality type, and its parameters. */
import { computed } from 'vue';
import { useMainStore } from '../../../store/main';
import { FORCE_KEY, paramDefaults } from '../../../utils/params';
import SettingsSection from './SettingsSection.vue';
import FormRow from '@focus/ui/components/ui/FormRow.vue';
import SelectField from '@focus/ui/components/ui/SelectField.vue';
import Banner from '@focus/ui/components/ui/Banner.vue';
import ParamSettingsForm from '../../config/ParamSettingsForm.vue';

const props = defineProps<{ index: number }>();
const store = useMainStore();

const modality = computed(() => store.config.modalities[props.index]!);
const enabled = computed(() =>
  store.config.perform_alignment && store.config.perform_registration && store.config.modalities.length >= 2,
);

const methods = computed<string[]>(() => {
  if (!store.schema) return ['none'];
  const compat = store.schema.registration_compatibility;
  return store.schema.registration_types.filter(rt => {
    const allowed = compat[rt];
    return allowed === null || allowed === undefined || allowed.includes(modality.value.type);
  });
});

const changeMethod = (registration_type: string) =>
  store.updateModality(props.index, {
    registration_type,
    registration_settings: paramDefaults(store.schema?.registration_params[registration_type]),
  });
</script>

<template>
  <SettingsSection
    :number="3"
    title="Registration"
    :description="`How features of ${modality.name} are transferred onto the reference spots.`"
  >
    <p v-if="!enabled" class="type-callout text-fg3">
      Registration is turned off for this run. Turn it on in the Modalities step or on Review.
    </p>
    <template v-else>
      <FormRow label="Registration method">
        <SelectField
          class="w-56"
          :model-value="modality.registration_type"
          aria-label="Registration method"
          @update:model-value="changeMethod"
        >
          <option v-for="rt in methods" :key="rt" :value="rt">{{ store.displayName(rt) }}</option>
        </SelectField>
      </FormRow>
      <ParamSettingsForm
        v-if="modality.registration_type !== 'none'"
        :specs="store.schema?.registration_params[modality.registration_type] ?? {}"
        :settings="modality.registration_settings"
        :exclude="[FORCE_KEY]"
        empty-text="null"
        @update="store.updateModality(index, { registration_settings: $event })"
      />
      <Banner v-if="modality.registration_type === 'feature_extraction'" tone="info">
        This method needs a HuggingFace token, which is set on Review.
      </Banner>
    </template>
  </SettingsSection>
</template>
