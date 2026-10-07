<script setup lang="ts">
/**
 * Section 2: how the reference is aligned onto this modality. A strategy
 * choice exists only when the reference type supports "pre-aligned";
 * otherwise alignment is manual and needs no settings.
 */
import { computed } from 'vue';
import { useMainStore } from '../../../store/main';
import type { Modality } from '../../../api/types';
import SettingsSection from './SettingsSection.vue';
import FormRow from '@focus/ui/components/ui/FormRow.vue';
import SelectField from '@focus/ui/components/ui/SelectField.vue';
import Banner from '@focus/ui/components/ui/Banner.vue';

const props = defineProps<{ index: number }>();
const store = useMainStore();

const modality = computed(() => store.config.modalities[props.index]!);
const enabled = computed(() => store.config.perform_alignment && store.config.modalities.length >= 2);

const strategyAvailable = computed(() => {
  const ref = store.config.modalities.find((m: Modality) => m.name === store.config.reference_modality);
  const compatible = store.schema?.alignment_strategy_compatibility['pre_aligned'];
  return !!ref && !!compatible && compatible.includes(ref.type);
});

// The reference can only be expressed in one coordinate system at a time,
// so only one target modality may use pre_aligned.
const preAlignedTaken = computed(() =>
  store.config.modalities.some((m: Modality, i: number) => i !== props.index && m.alignment_strategy === 'pre_aligned'),
);
</script>

<template>
  <SettingsSection
    :number="2"
    title="Alignment"
    :description="`How ${store.config.reference_modality} is placed in the coordinate space of ${modality.name}.`"
  >
    <p v-if="!enabled" class="type-callout text-fg3">
      Alignment is turned off for this run. Turn it on in the Modalities step or on Review.
    </p>
    <template v-else-if="strategyAvailable">
      <FormRow label="Alignment strategy">
        <SelectField
          class="w-56"
          :model-value="modality.alignment_strategy"
          aria-label="Alignment strategy"
          @update:model-value="store.updateModality(index, { alignment_strategy: $event })"
        >
          <option value="manual">Manual alignment</option>
          <option
            value="pre_aligned"
            :disabled="preAlignedTaken"
            :title="preAlignedTaken ? 'Another modality already uses Pre-aligned.' : ''"
          >Pre-aligned</option>
        </SelectField>
      </FormRow>
      <Banner v-if="preAlignedTaken && modality.alignment_strategy !== 'pre_aligned'" tone="neutral">
        Pre-aligned is unavailable: another modality is already using it. The reference modality can only be
        pre-aligned to one coordinate system at a time.
      </Banner>
      <Banner v-if="modality.alignment_strategy === 'pre_aligned'" tone="warning">
        The reference modality's coordinates are assumed to be already expressed in this modality's coordinate
        system. No interactive alignment will be performed.
      </Banner>
    </template>
    <p v-else class="type-callout text-fg3">
      Aligned manually in the alignment tool during the run. No settings needed.
    </p>
  </SettingsSection>
</template>
