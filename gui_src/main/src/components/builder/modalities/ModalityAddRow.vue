<script setup lang="ts">
/** Last row of the modality list: name and type of a new modality, then Add. */
import { ref, watch } from 'vue';
import { useMainStore } from '../../../store/main';
import { uniqueNameValidator } from '../../../composables/useInlineEntry';
import TextField from '@focus/ui/components/ui/TextField.vue';
import SelectField from '@focus/ui/components/ui/SelectField.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';

const store = useMainStore();

const name = ref('');
const type = ref('');
const error = ref('');
const validate = uniqueNameValidator(() => store.modalityNames, 'modality');

watch(() => store.schema?.modality_types, types => { if (!type.value && types?.length) type.value = types[0]!; }, { immediate: true });

const add = () => {
  const value = name.value.trim();
  error.value = validate(value) ?? '';
  if (error.value) return;
  store.addModality(value, type.value);
  name.value = '';
};
</script>

<template>
  <div class="modality-columns grid items-start gap-3 pt-3">
    <span />
    <div class="flex min-w-0 flex-col gap-1">
      <TextField
        v-model="name"
        placeholder="New modality name, e.g. Visium"
        aria-label="New modality name"
        :invalid="!!error"
        @keyup.enter="add"
      />
      <p v-if="error" class="type-footnote text-danger-fg">{{ error }}</p>
    </div>
    <SelectField v-model="type" aria-label="New modality type">
      <option v-for="t in store.schema?.modality_types" :key="t" :value="t">{{ store.displayName(t) }}</option>
    </SelectField>
    <span />
    <div class="col-start-2 col-end-4">
      <BaseButton variant="tinted" icon="plus" @click="add">Add modality</BaseButton>
    </div>
  </div>
</template>

