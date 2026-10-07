<script setup lang="ts">
/**
 * One modality in the overview: reference radio, editable name, type, remove.
 * Type and reference changes that would discard settings ask first.
 */
import { computed, ref, watch } from 'vue';
import { useMainStore } from '../../../store/main';
import { useBuilderStore } from '../../../store/builder';
import { useDialog } from '../../../composables/useDialog';
import { hasCustomSettings } from '../../../utils/params';
import type { Modality } from '../../../api/types';
import RadioDot from '@focus/ui/components/ui/RadioDot.vue';
import TextField from '@focus/ui/components/ui/TextField.vue';
import SelectField from '@focus/ui/components/ui/SelectField.vue';
import IconButton from '@focus/ui/components/ui/IconButton.vue';

const props = defineProps<{ index: number }>();

const store = useMainStore();
const builder = useBuilderStore();
const { showConfirm } = useDialog();

const modality = computed(() => store.config.modalities[props.index]!);
const isReference = computed(() => modality.value.name === store.config.reference_modality);

// Name: local draft, committed on Enter or blur; invalid names revert.
const draft = ref('');
const nameError = ref('');
watch(() => modality.value.name, name => { draft.value = name; }, { immediate: true });

const commitName = () => {
  const name = draft.value.trim();
  const others = store.config.modalities.filter((_: Modality, i: number) => i !== props.index).map((m: Modality) => m.name);
  nameError.value = !name ? 'A name is required.' : others.includes(name) ? 'This name is already used.' : '';
  if (nameError.value) return;
  if (name === modality.value.name) return;
  builder.renameVisited(modality.value.name, name);
  store.updateModality(props.index, { name });
};

const revertName = () => {
  draft.value = modality.value.name;
  nameError.value = '';
};

// Re-render the select after a cancelled change so it shows the kept value.
const typeKey = ref(0);

const changeType = async (type: string) => {
  const m = modality.value;
  if (type === m.type) return;
  if (hasCustomSettings(m) || builder.visited.includes(m.name)) {
    const ok = await showConfirm({
      message: `Change "${m.name}" to ${store.displayName(type)}? Its preprocessing, alignment and registration settings will be reset to defaults.`,
      confirmLabel: 'Change type',
    });
    if (!ok) {
      typeKey.value += 1;
      return;
    }
  }
  store.changeModalityType(props.index, type);
  builder.forgetVisited(m.name);
};

const makeReference = async () => {
  const m = modality.value;
  if (isReference.value) return;
  if (m.registration_type !== 'none') {
    const ok = await showConfirm({
      message: `Make "${m.name}" the reference? The reference is never registered, so its registration settings will be cleared.`,
      confirmLabel: 'Make reference',
    });
    if (!ok) return;
  }
  store.setReferenceModality(m.name);
};

const remove = async () => {
  const ok = await showConfirm({
    message: `Remove modality "${modality.value.name}"?`,
    confirmLabel: 'Remove',
    variant: 'danger',
  });
  if (!ok) return;
  builder.forgetVisited(modality.value.name);
  store.removeModality(props.index);
  builder.clampActiveModality();
};
</script>

<template>
  <div class="modality-columns grid items-start gap-3 py-3">
    <div class="flex h-[30px] items-center justify-center">
      <RadioDot :checked="isReference" :label="`Use ${modality.name} as reference`" @select="makeReference" />
    </div>
    <div class="flex min-w-0 flex-col gap-1">
      <TextField
        v-model="draft"
        aria-label="Modality name"
        :invalid="!!nameError"
        @change="commitName"
        @keyup.enter="commitName"
        @keyup.escape="revertName"
      />
      <p v-if="nameError" class="type-footnote text-danger-fg">{{ nameError }}</p>
      <p v-else-if="isReference" class="type-footnote text-primary-fg">Reference modality</p>
    </div>
    <SelectField
      :key="typeKey"
      :model-value="modality.type"
      aria-label="Modality type"
      @update:model-value="changeType"
    >
      <option v-for="t in store.schema?.modality_types" :key="t" :value="t">{{ store.displayName(t) }}</option>
    </SelectField>
    <IconButton icon="trash" label="Remove modality" tone="danger" @click="remove" />
  </div>
</template>

