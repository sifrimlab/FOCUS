<script setup lang="ts">
/** Deletes per-sample intermediate files after an inline confirmation. */
import { computed, ref } from 'vue';
import { useMainStore } from '../../store/main';
import type { OutputFiles } from '../../api/types';
import { pluralize } from '../../utils/format';
import GlassCard from '@focus/ui/components/ui/GlassCard.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import AppIcon from '@focus/ui/components/ui/AppIcon.vue';

const props = defineProps<{ files: OutputFiles }>();
const store = useMainStore();

const confirming = ref(false);
const done = ref(false);

const perSampleCount = computed(() =>
  (Object.keys(props.files) as (keyof OutputFiles)[])
    .filter(key => key !== 'multimodal')
    .reduce((sum, key) => {
      const perModality = props.files[key]?.per_modality ?? {};
      return sum + Object.values(perModality).reduce((n, paths) => n + paths.length, 0);
    }, 0),
);

const cleanup = async () => {
  await store.cleanupFiles();
  confirming.value = false;
  done.value = true;
};
</script>

<template>
  <GlassCard v-if="perSampleCount > 0 || done" class="flex flex-col gap-3">
    <p class="type-callout text-fg2">
      Per-sample intermediate files take extra disk space and are not needed once the merged outputs are available.
    </p>

    <Transition name="fade" mode="out-in">
      <p v-if="done" class="type-body-strong text-success-fg flex items-center gap-1.5">
        <AppIcon name="check-circle" /> Temporary files deleted.
      </p>
      <div v-else-if="confirming" class="flex flex-wrap items-center gap-2">
        <span class="type-body text-danger-fg mr-1">Delete {{ pluralize(perSampleCount, 'per-sample file') }}?</span>
        <BaseButton variant="destructive-filled" @click="cleanup">Confirm</BaseButton>
        <BaseButton @click="confirming = false">Cancel</BaseButton>
      </div>
      <div v-else>
        <BaseButton icon="trash" @click="confirming = true">Clean temporary files</BaseButton>
      </div>
    </Transition>
  </GlassCard>
</template>
