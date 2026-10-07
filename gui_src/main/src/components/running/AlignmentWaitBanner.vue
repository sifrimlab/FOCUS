<script setup lang="ts">
/** Manual alignment prompt with the link to the alignment GUI. */
import Banner from '@focus/ui/components/ui/Banner.vue';
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';

const props = defineProps<{ port: number }>();

const openAlignmentTool = () => window.open(`http://localhost:${props.port}`, '_blank');
</script>

<template>
  <Banner tone="warning" title="Manual alignment required" standalone>
    The alignment tool will open in a new tab. Complete the alignment for each sample, then return here.
    <template #actions>
      <BaseButton variant="primary" size="xl" icon="arrow-top-right-on-square" @click="openAlignmentTool">
        Open alignment tool
      </BaseButton>
    </template>
  </Banner>
</template>
