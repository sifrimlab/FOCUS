<script setup lang="ts">
/** One output category: merged files, then per-sample files grouped by modality. */
import { computed } from 'vue';
import type { OutputSection } from '../../api/types';
import { basename } from '../../utils/format';
import GlassCard from '@focus/ui/components/ui/GlassCard.vue';
import CardHeader from '@focus/ui/components/ui/CardHeader.vue';
import InsetWell from '@focus/ui/components/ui/InsetWell.vue';
import Disclosure from '@focus/ui/components/ui/Disclosure.vue';

const props = defineProps<{
  title: string;
  section: OutputSection;
}>();

const perModality = computed(() =>
  Object.entries(props.section.per_modality).filter(([, paths]) => paths.length > 0),
);
const perModalityTotal = computed(() => perModality.value.reduce((sum, [, paths]) => sum + paths.length, 0));
</script>

<template>
  <GlassCard class="flex flex-col gap-3">
    <CardHeader :title="title" level="section" />

    <div v-if="section.merged.length > 0" class="flex flex-col gap-1.5">
      <span class="type-footnote text-fg3">Main output</span>
      <InsetWell v-for="f in section.merged" :key="f" padding="none" class="type-mono-small text-fg1 px-3 py-1.5" :title="f">
        {{ basename(f) }}
      </InsetWell>
    </div>

    <Disclosure v-if="perModalityTotal > 0" :title="`Per-sample files (${perModalityTotal})`" compact>
      <div class="flex flex-col gap-2">
        <div v-for="[modality, paths] in perModality" :key="modality" class="flex flex-col gap-0.5">
          <span class="type-footnote text-fg3 pl-2">{{ modality }}</span>
          <div class="flex flex-col gap-0.5 border-l-2 border-separator pl-2">
            <span v-for="f in paths" :key="f" class="type-mono-small text-fg2 py-0.5" :title="f">{{ basename(f) }}</span>
          </div>
        </div>
      </div>
    </Disclosure>
  </GlassCard>
</template>
