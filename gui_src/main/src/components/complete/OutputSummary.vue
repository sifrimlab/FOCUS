<script setup lang="ts">
/** All non-empty output categories, in presentation order. */
import { computed } from 'vue';
import type { OutputFiles, OutputSection as Section } from '../../api/types';
import OutputSection from './OutputSection.vue';

const props = defineProps<{ files: OutputFiles }>();

const SECTIONS: { key: keyof OutputFiles; label: string }[] = [
  { key: 'multimodal', label: 'Multimodal dataset' },
  { key: 'registration', label: 'Registration artifacts' },
  { key: 'annotations', label: 'Annotated artifacts' },
  { key: 'alignment', label: 'Aligned artifacts' },
  { key: 'preprocessing', label: 'Preprocessed artifacts' },
];

const hasContent = (section: Section | undefined): section is Section =>
  !!section && (section.merged.length > 0 || Object.values(section.per_modality).some(p => p.length > 0));

const visible = computed(() =>
  SECTIONS.flatMap(({ key, label }) => {
    const section = props.files[key];
    return hasContent(section) ? [{ key, label, section }] : [];
  }),
);
</script>

<template>
  <div v-if="visible.length > 0" class="flex flex-col gap-3">
    <OutputSection v-for="v in visible" :key="v.key" :title="v.label" :section="v.section" />
  </div>
</template>
