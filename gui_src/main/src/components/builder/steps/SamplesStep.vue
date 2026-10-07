<script setup lang="ts">
/** Step 1: choose which samples take part in the run, filter them, add a sample folder. */
import { computed, ref } from 'vue';
import { useMainStore } from '../../../store/main';
import { uniqueNameValidator, useInlineEntry } from '../../../composables/useInlineEntry';
import { pluralize } from '../../../utils/format';
import StepFrame from '../StepFrame.vue';
import GlassCard from '@focus/ui/components/ui/GlassCard.vue';
import TextField from '@focus/ui/components/ui/TextField.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import InlineEntryForm from '@focus/ui/components/ui/InlineEntryForm.vue';
import Banner from '@focus/ui/components/ui/Banner.vue';
import ToggleChip from '@focus/ui/components/ui/ToggleChip.vue';
import EmptyState from '@focus/ui/components/ui/EmptyState.vue';

const store = useMainStore();

const entry = useInlineEntry({
  validate: uniqueNameValidator(() => store.samples, 'sample'),
  submit: name => store.addManualSample(name),
});

const filter = ref('');
const visible = computed(() => {
  const q = filter.value.trim().toLowerCase();
  return q ? store.samples.filter(id => id.toLowerCase().includes(q)) : store.samples;
});
const filtering = computed(() => filter.value.trim() !== '');

const includedCount = computed(() => store.samples.filter(id => !store.config.ignore_samples.includes(id)).length);
const manual = computed(() => store.manuallyAddedSamples);

const isIncluded = (id: string) => !store.config.ignore_samples.includes(id);

const setIncluded = (ids: string[], on: boolean) => {
  const ignored = new Set(store.config.ignore_samples);
  for (const id of ids) {
    if (on) ignored.delete(id);
    else ignored.add(id);
  }
  store.config.ignore_samples = store.samples.filter(id => ignored.has(id));
  store.triggerAutoSave();
};
</script>

<template>
  <StepFrame
    title="Samples"
    description="Choose which samples take part in this run. Excluded samples are skipped entirely."
  >
    <template #toolbar>
      <BaseButton variant="tinted" icon="plus" :disabled="entry.isOpen.value" @click="entry.open()">Add sample</BaseButton>
    </template>

    <Transition name="fade">
      <GlassCard v-if="entry.isOpen.value">
        <InlineEntryForm
          :ref="entry.inputRef"
          v-model="entry.value.value"
          label="New sample name. A folder with this name is created in the dataset."
          placeholder="Sample name"
          busy-label="Creating…"
          :error="entry.error.value"
          :busy="entry.busy.value"
          @confirm="entry.confirm()"
          @cancel="entry.cancel()"
        />
      </GlassCard>
    </Transition>

    <Banner v-if="manual.length > 0" tone="warning">
      {{ pluralize(manual.length, 'sample folder') }} {{ manual.length > 1 ? 'were' : 'was' }} created here
      ({{ manual.join(', ') }}). Populate {{ manual.length > 1 ? 'them' : 'it' }} with data files before starting
      processing.
    </Banner>

    <GlassCard class="flex flex-col gap-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <TextField
          v-model="filter"
          icon="magnifying-glass"
          class="w-64"
          placeholder="Filter samples"
          aria-label="Filter samples"
        />
        <div class="flex items-center gap-1">
          <span class="type-footnote nums text-fg3 mr-2">{{ includedCount }} of {{ store.samples.length }} included</span>
          <BaseButton variant="plain" :disabled="visible.length === 0" @click="setIncluded(visible, true)">
            {{ filtering ? `Include ${visible.length} shown` : 'Include all' }}
          </BaseButton>
          <BaseButton variant="plain" :disabled="visible.length === 0" @click="setIncluded(visible, false)">
            {{ filtering ? `Exclude ${visible.length} shown` : 'Exclude all' }}
          </BaseButton>
        </div>
      </div>

      <EmptyState v-if="store.samples.length === 0" icon="folder">
        No sample folders were found in the dataset. Add a sample to continue.
      </EmptyState>
      <EmptyState v-else-if="visible.length === 0" icon="magnifying-glass">
        No sample matches "{{ filter }}".
      </EmptyState>
      <div v-else class="grid grid-cols-[repeat(auto-fill,minmax(176px,1fr))] gap-2">
        <ToggleChip
          v-for="id in visible"
          :key="id"
          size="lg"
          :model-value="isIncluded(id)"
          :title="id"
          @update:model-value="setIncluded([id], $event)"
        >{{ id }}</ToggleChip>
      </div>
    </GlassCard>
  </StepFrame>
</template>
