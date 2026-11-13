<script setup lang="ts">
/**
 * Section 2: how the reference is aligned onto this modality. A strategy
 * choice exists only when the reference type supports "pre-aligned".
 * A spot-based modality aligned manually can instead be aligned to a guide:
 * another modality already aligned to the reference (typically the H&E of
 * the reference's section, when this modality comes from a consecutive cut).
 */
import { computed } from 'vue';
import { useMainStore } from '../../../store/main';
import type { Modality } from '../../../api/types';
import SettingsSection from './SettingsSection.vue';
import FormRow from '@focus/ui/components/ui/FormRow.vue';
import SelectField from '@focus/ui/components/ui/SelectField.vue';
import Banner from '@focus/ui/components/ui/Banner.vue';
import { SPOT_TYPES } from '../../../utils/runPlan';

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

/** Modalities guided by this one: a guide must itself be aligned to the reference directly. */
const guiding = computed(() =>
  store.config.modalities.filter((m: Modality) => m.alignment_guide === modality.value.name).map(m => m.name),
);

/** Other non-reference modalities aligned directly to the reference. */
const guideOptions = computed(() =>
  store.config.modalities.filter((m: Modality, i: number) =>
    i !== props.index && m.name && m.name !== store.config.reference_modality && !m.alignment_guide,
  ).map(m => m.name),
);

const guideAvailable = computed(() =>
  SPOT_TYPES.includes(modality.value.type)
  && modality.value.alignment_strategy === 'manual'
  && guiding.value.length === 0
  && (guideOptions.value.length > 0 || !!modality.value.alignment_guide),
);

const setStrategy = (strategy: string) =>
  store.updateModality(props.index, strategy === 'manual' ? { alignment_strategy: strategy } : { alignment_strategy: strategy, alignment_guide: null });

const setGuide = (guide: string) => store.updateModality(props.index, { alignment_guide: guide || null });
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
          @update:model-value="setStrategy($event)"
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
    <p v-else-if="!guideAvailable" class="type-callout text-fg3">
      Aligned manually in the alignment tool during the run. No settings needed.
    </p>
    <template v-if="enabled && guideAvailable">
      <FormRow label="Align via">
        <SelectField
          class="w-56"
          :model-value="modality.alignment_guide ?? ''"
          aria-label="Align via"
          @update:model-value="setGuide($event)"
        >
          <option value="">{{ store.config.reference_modality }} (directly)</option>
          <option v-for="g in guideOptions" :key="g" :value="g">{{ g }}</option>
        </SelectField>
      </FormRow>
      <Banner v-if="modality.alignment_guide" tone="info">
        The alignment tool shows {{ modality.name }} over {{ modality.alignment_guide }}, which is often easier
        to judge when they come from different sections. {{ store.config.reference_modality }} then reaches
        {{ modality.name }} through its own alignment to {{ modality.alignment_guide }}.
      </Banner>
    </template>
    <p v-if="enabled && guiding.length" class="type-footnote text-fg3">
      Guides the alignment of {{ guiding.join(', ') }}, so it is aligned to {{ store.config.reference_modality }} directly.
    </p>
  </SettingsSection>
</template>
