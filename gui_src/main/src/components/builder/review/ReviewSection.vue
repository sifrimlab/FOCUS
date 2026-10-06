<script setup lang="ts">
/** A titled Review section with an optional "Edit" link to the step that owns it. */
import GlassCard from '../../ui/GlassCard.vue';
import CardHeader from '../../ui/CardHeader.vue';
import BaseButton from '../../ui/BaseButton.vue';

defineProps<{ title: string; editLabel?: string }>();
const emit = defineEmits<{ edit: [] }>();
</script>

<template>
  <GlassCard class="flex flex-col gap-4">
    <CardHeader :title="title">
      <template v-if="$slots.meta" #meta><slot name="meta" /></template>
      <template v-if="editLabel" #actions>
        <BaseButton variant="plain" icon="pencil-square" @click="emit('edit')">{{ editLabel }}</BaseButton>
      </template>
    </CardHeader>
    <slot />
  </GlassCard>
</template>
