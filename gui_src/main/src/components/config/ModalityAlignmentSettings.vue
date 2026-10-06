<script setup lang="ts">
/**
 * Alignment strategy and force-recompute settings of one target modality.
 * Rendered only for non-reference modalities when alignment is enabled and
 * the reference modality type supports the pre-aligned strategy.
 */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import type { Modality } from '../../api/types';
import FormRow from '../ui/FormRow.vue';
import SelectField from '../ui/SelectField.vue';
import ToggleSwitch from '../ui/ToggleSwitch.vue';
import Banner from '../ui/Banner.vue';

const props = defineProps<{ index: number }>();
const store = useMainStore();

const modality = computed(() => store.config.modalities[props.index]!);

// Spot-based references have meaningful coordinates that may already be
// expressed in another modality's coordinate system.
const visible = computed(() => {
  if (!store.schema || !store.config.perform_alignment) return false;
  if (modality.value.name === store.config.reference_modality) return false;
  const ref = store.config.modalities.find((m: Modality) => m.name === store.config.reference_modality);
  if (!ref) return false;
  const compatible = store.schema.alignment_strategy_compatibility['pre_aligned'];
  return compatible !== null && compatible !== undefined && compatible.includes(ref.type);
});

// The reference can only be expressed in one coordinate system at a time,
// so only one target modality may use pre_aligned.
const preAlignedTaken = computed(() =>
  store.config.modalities.some((m: Modality, i: number) => i !== props.index && m.alignment_strategy === 'pre_aligned'),
);

const update = (changes: Partial<Modality>) => store.updateModality(props.index, changes);
</script>

<template>
  <template v-if="visible">
    <FormRow label="Alignment strategy">
      <SelectField
        class="w-56"
        :model-value="modality.alignment_strategy"
        aria-label="Alignment strategy"
        @update:model-value="update({ alignment_strategy: $event })"
      >
        <option value="manual">Manual alignment</option>
        <option
          value="pre_aligned"
          :disabled="preAlignedTaken"
          :title="preAlignedTaken ? 'Another modality already uses Pre-aligned. The reference can only be expressed in one coordinate system at a time.' : ''"
        >Pre-aligned</option>
      </SelectField>
    </FormRow>

    <FormRow label="Force alignment recomputing">
      <ToggleSwitch
        tone="warning"
        label="Force alignment recomputing"
        :model-value="modality.alignment_force_recomputing"
        @update:model-value="update({ alignment_force_recomputing: $event })"
      />
    </FormRow>

    <Banner v-if="preAlignedTaken && modality.alignment_strategy !== 'pre_aligned'" tone="neutral">
      Pre-aligned is unavailable: another modality is already using it. The reference modality can only be pre-aligned
      to one coordinate system at a time.
    </Banner>

    <Banner v-if="modality.alignment_strategy === 'pre_aligned'" tone="warning">
      The reference modality's coordinates are assumed to be already expressed in this modality's coordinate system.
      No interactive alignment will be performed.
    </Banner>
  </template>
</template>
