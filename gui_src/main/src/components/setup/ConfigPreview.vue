<script setup lang="ts">
/** Summary of an uploaded config before it is applied. Presentational only. */
import { computed } from 'vue';
import { useMainStore } from '../../store/main';
import type { ConfigSummary } from '../../utils/configFile';
import { pluralize } from '../../utils/format';
import InsetWell from '@focus/ui/components/ui/InsetWell.vue';
import IconButton from '@focus/ui/components/ui/IconButton.vue';
import AppIcon from '@focus/ui/components/ui/AppIcon.vue';

const props = defineProps<{
  fileName: string;
  summary: ConfigSummary | null;
}>();

const emit = defineEmits<{ clear: [] }>();

const store = useMainStore();

const pathDiffers = computed(
  () => !!props.summary?.datasetPath && props.summary.datasetPath !== store.config.dataset_path,
);

const steps = computed(() => {
  const s = props.summary;
  if (!s) return '';
  const optional = [s.alignment && 'alignment', s.annotations && 'annotation transfer', s.registration && 'registration'];
  return ['Preprocessing', ...optional.filter(Boolean)].join(', ');
});
</script>

<template>
  <InsetWell class="flex flex-col gap-3">
    <div class="flex items-center gap-2">
      <AppIcon name="document-text" class="text-fg3" />
      <span class="type-mono text-fg1 ellipsis flex-1" :title="fileName">{{ fileName }}</span>
      <IconButton icon="x-mark" label="Choose another file" size="sm" @click="emit('clear')" />
    </div>

    <dl v-if="summary" class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 border-t border-separator pt-3">
      <dt class="type-footnote text-fg3">Modalities</dt>
      <dd class="flex flex-wrap gap-1.5">
        <span v-if="summary.modalities.length === 0" class="type-footnote text-fg3">None defined</span>
        <span
          v-for="m in summary.modalities"
          :key="m.name"
          class="type-caption rounded-full bg-primary-soft px-2 py-0.5 text-primary-fg"
        >
          {{ m.name }}<span v-if="m.type" class="text-fg3"> · {{ store.displayName(m.type) }}</span>
        </span>
      </dd>

      <dt class="type-footnote text-fg3">Reference</dt>
      <dd class="type-footnote text-fg1">{{ summary.reference || 'Not set' }}</dd>

      <dt class="type-footnote text-fg3">Steps</dt>
      <dd class="type-footnote text-fg1">{{ steps }}</dd>

      <dt class="type-footnote text-fg3">Samples</dt>
      <dd class="type-footnote nums text-fg1">
        {{ summary.ignoredSamples ? `${pluralize(summary.ignoredSamples, 'sample')} disabled` : 'All discovered samples enabled' }}
      </dd>
    </dl>

    <p v-if="pathDiffers" class="type-footnote text-fg3 flex items-start gap-1.5">
      <AppIcon name="information-circle" :size="12" class="mt-0.5" />
      <span>
        The file points to <code class="type-mono-small">{{ summary?.datasetPath }}</code>. The dataset path will be set
        to the selected folder.
      </span>
    </p>
  </InsetWell>
</template>
