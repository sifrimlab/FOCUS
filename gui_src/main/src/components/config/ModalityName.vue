<script setup lang="ts">
/**
 * Modality title with inline rename. Enter or blur saves, Escape cancels.
 * Empty names and names used by another modality are ignored.
 */
import { nextTick, ref } from 'vue';
import TextField from '../ui/TextField.vue';
import IconButton from '../ui/IconButton.vue';

const props = defineProps<{
  name: string;
  takenNames: string[];
}>();

const emit = defineEmits<{ rename: [name: string] }>();

const editing = ref(false);
const draft = ref('');
const field = ref<InstanceType<typeof TextField> | null>(null);

const start = async () => {
  draft.value = props.name;
  editing.value = true;
  await nextTick();
  field.value?.focus();
  field.value?.select();
};

const save = () => {
  if (!editing.value) return;
  editing.value = false;
  const name = draft.value.trim();
  if (name && name !== props.name && !props.takenNames.includes(name)) emit('rename', name);
};

defineExpose({ start });
</script>

<template>
  <TextField
    v-if="editing"
    ref="field"
    v-model="draft"
    class="flex-1"
    aria-label="Modality name"
    @keyup.enter="save"
    @keyup.escape="editing = false"
    @blur="save"
  />
  <div v-else class="flex min-w-0 items-center gap-1">
    <h3 class="type-headline text-fg1 ellipsis">{{ name }}</h3>
    <IconButton icon="pencil-square" label="Edit name" size="sm" @click="start" />
  </div>
</template>
