<script setup lang="ts">
/** List of modalities with add (inline name entry) and remove-all actions. */
import { useMainStore } from '../../store/main';
import { useDialog } from '../../composables/useDialog';
import { uniqueNameValidator, useInlineEntry } from '../../composables/useInlineEntry';
import GlassCard from '../ui/GlassCard.vue';
import CardHeader from '../ui/CardHeader.vue';
import IconButton from '../ui/IconButton.vue';
import InlineEntryForm from '../ui/InlineEntryForm.vue';
import EmptyState from '../ui/EmptyState.vue';
import ModalityCard from './ModalityCard.vue';

const store = useMainStore();
const { showConfirm } = useDialog();

const entry = useInlineEntry({
  validate: uniqueNameValidator(() => store.modalityNames, 'modality'),
  submit: name => store.addModality(name),
});

const confirmRemoveAll = async () => {
  const ok = await showConfirm({
    message: 'Remove all modalities? This cannot be undone.',
    confirmLabel: 'Remove all',
    variant: 'danger',
  });
  if (ok) store.removeAllModalities();
};
</script>

<template>
  <GlassCard class="flex flex-col gap-4">
    <CardHeader title="Modalities">
      <template #meta>{{ store.config.modalities.length || '' }}</template>
      <template #actions>
        <IconButton icon="plus" label="Add modality" tone="primary" @click="entry.open()" />
        <IconButton
          v-if="store.config.modalities.length > 0"
          icon="trash"
          label="Remove all modalities"
          tone="danger"
          @click="confirmRemoveAll"
        />
      </template>
    </CardHeader>

    <Transition name="fade">
      <InlineEntryForm
        v-if="entry.isOpen.value"
        :ref="entry.inputRef"
        v-model="entry.value.value"
        label="New modality name"
        placeholder="e.g., Fluorescence"
        :error="entry.error.value"
        :busy="entry.busy.value"
        @confirm="entry.confirm()"
        @cancel="entry.cancel()"
      />
    </Transition>

    <TransitionGroup name="list" tag="div" class="flex flex-col gap-3">
      <ModalityCard v-for="(_, i) in store.config.modalities" :key="i" :index="i" />
    </TransitionGroup>

    <EmptyState v-if="store.config.modalities.length === 0 && !entry.isOpen.value" icon="plus">
      No modalities yet. Click + to add one.
    </EmptyState>
  </GlassCard>
</template>
