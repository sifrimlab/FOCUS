<script setup lang="ts">
/** Settings of one modality, nested in the Modalities card as an inset well. */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import { useDialog } from '../../composables/useDialog';
import { paramDefaults } from '../../utils/params';
import type { Modality } from '../../api/types';
import InsetWell from '../ui/InsetWell.vue';
import CardHeader from '../ui/CardHeader.vue';
import IconButton from '../ui/IconButton.vue';
import FormRow from '../ui/FormRow.vue';
import SelectField from '../ui/SelectField.vue';
import Disclosure from '../ui/Disclosure.vue';
import ModalityName from './ModalityName.vue';
import ParamSettingsForm from './ParamSettingsForm.vue';
import ModalityAlignmentSettings from './ModalityAlignmentSettings.vue';

const props = defineProps<{ index: number }>();
const store = useMainStore();
const { showConfirm } = useDialog();

const modality = computed(() => store.config.modalities[props.index]!);
const isReference = computed(() => modality.value.name === store.config.reference_modality);
const otherNames = computed(() =>
  store.config.modalities.filter((_: Modality, i: number) => i !== props.index).map((m: Modality) => m.name),
);

const compatibleRegistrationTypes = computed<string[]>(() => {
  if (!store.schema) return ['none'];
  const compat = store.schema.registration_compatibility;
  return store.schema.registration_types.filter(rt => {
    const allowed = compat[rt];
    return allowed === null || allowed === undefined || allowed.includes(modality.value.type);
  });
});

const update = (changes: Partial<Modality>) => store.updateModality(props.index, changes);

const changeType = (type: string) => update({
  type,
  processing_settings: paramDefaults(store.schema?.processing_params[type]),
  registration_type: 'none',
  registration_settings: {},
  alignment_strategy: 'manual',
});

const changeRegistrationType = (registration_type: string) => update({
  registration_type,
  registration_settings: paramDefaults(store.schema?.registration_params[registration_type]),
});

const confirmRemove = async () => {
  const ok = await showConfirm({
    message: `Remove modality "${modality.value.name}"?`,
    confirmLabel: 'Remove',
    variant: 'danger',
  });
  if (ok) store.removeModality(props.index);
};
</script>

<template>
  <InsetWell class="flex flex-col gap-4">
    <CardHeader level="section">
      <template #title>
        <ModalityName :name="modality.name" :taken-names="otherNames" @rename="update({ name: $event })" />
      </template>
      <template #actions>
        <IconButton icon="trash" label="Remove modality" size="sm" tone="danger" @click="confirmRemove" />
      </template>
    </CardHeader>

    <FormRow label="Type">
      <SelectField class="w-56" :model-value="modality.type" aria-label="Modality type" @update:model-value="changeType">
        <option v-for="t in store.schema?.modality_types" :key="t" :value="t">{{ store.displayName(t) }}</option>
      </SelectField>
    </FormRow>

    <Disclosure title="Processing settings" open>
      <ParamSettingsForm
        :specs="store.schema?.processing_params[modality.type] ?? {}"
        :settings="modality.processing_settings"
        empty-text="nullable"
        @update="update({ processing_settings: $event })"
      />
    </Disclosure>

    <ModalityAlignmentSettings :index="index" />

    <template v-if="store.config.perform_registration && !isReference">
      <FormRow label="Registration type">
        <SelectField
          class="w-56"
          :model-value="modality.registration_type"
          aria-label="Registration type"
          @update:model-value="changeRegistrationType"
        >
          <option v-for="rt in compatibleRegistrationTypes" :key="rt" :value="rt">{{ store.displayName(rt) }}</option>
        </SelectField>
      </FormRow>

      <Disclosure v-if="modality.registration_type !== 'none'" title="Registration settings" open>
        <ParamSettingsForm
          :specs="store.schema?.registration_params[modality.registration_type] ?? {}"
          :settings="modality.registration_settings"
          empty-text="null"
          @update="update({ registration_settings: $event })"
        />
      </Disclosure>
    </template>
  </InsetWell>
</template>
