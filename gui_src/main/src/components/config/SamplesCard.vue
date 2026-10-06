<script setup lang="ts">
/** Sample inclusion: toggle samples, enable/disable all, add a sample folder. */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import { uniqueNameValidator, useInlineEntry } from '../../composables/useInlineEntry';
import { pluralize } from '../../utils/format';
import GlassCard from '../ui/GlassCard.vue';
import CardHeader from '../ui/CardHeader.vue';
import BaseButton from '../ui/BaseButton.vue';
import IconButton from '../ui/IconButton.vue';
import InlineEntryForm from '../ui/InlineEntryForm.vue';
import Banner from '../ui/Banner.vue';
import ToggleChip from '../ui/ToggleChip.vue';

const store = useMainStore();

const entry = useInlineEntry({
  validate: uniqueNameValidator(() => store.samples, 'sample'),
  submit: name => store.addManualSample(name),
});

const activeCount = computed(() => store.samples.length - store.config.ignore_samples.length);
const manual = computed(() => store.manuallyAddedSamples);

const isIncluded = (id: string) => !store.config.ignore_samples.includes(id);

const setIncluded = (id: string, on: boolean) => {
  const ignored = store.config.ignore_samples;
  const idx = ignored.indexOf(id);
  if (on && idx !== -1) ignored.splice(idx, 1);
  if (!on && idx === -1) ignored.push(id);
  store.triggerAutoSave();
};

const setAll = (on: boolean) => {
  store.config.ignore_samples = on ? [] : [...store.samples];
  store.triggerAutoSave();
};
</script>

<template>
  <GlassCard class="flex flex-col gap-4">
    <CardHeader title="Samples">
      <template #meta>{{ activeCount }}&thinsp;/&thinsp;{{ store.samples.length }} active</template>
      <template #actions>
        <BaseButton variant="plain" @click="setAll(true)">Enable all</BaseButton>
        <BaseButton variant="plain" @click="setAll(false)">Disable all</BaseButton>
        <IconButton icon="plus" label="Add new sample" tone="primary" :disabled="entry.isOpen.value" @click="entry.open()" />
      </template>
    </CardHeader>

    <Transition name="fade">
      <InlineEntryForm
        v-if="entry.isOpen.value"
        :ref="entry.inputRef"
        v-model="entry.value.value"
        label="New sample name"
        placeholder="Sample name"
        busy-label="Creating…"
        :error="entry.error.value"
        :busy="entry.busy.value"
        @confirm="entry.confirm()"
        @cancel="entry.cancel()"
      />
    </Transition>

    <Banner v-if="manual.length > 0" tone="warning">
      {{ pluralize(manual.length, 'sample folder') }} {{ manual.length > 1 ? 'were' : 'was' }} manually created
      ({{ manual.join(', ') }}). You must populate {{ manual.length > 1 ? 'them' : 'it' }} with data files before
      starting processing.
    </Banner>

    <p v-if="store.samples.length === 0" class="type-callout text-fg3 py-3 text-center">No samples discovered.</p>
    <div v-else class="flex flex-wrap gap-2">
      <ToggleChip
        v-for="id in store.samples"
        :key="id"
        :model-value="isIncluded(id)"
        @update:model-value="setIncluded(id, $event)"
      >{{ id }}</ToggleChip>
    </div>

    <p class="type-footnote text-fg3">
      Click a sample to toggle it on or off. Disabled samples are fully skipped during processing.
    </p>
  </GlassCard>
</template>
