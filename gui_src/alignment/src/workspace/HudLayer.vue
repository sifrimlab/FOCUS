<script setup lang="ts">
/**
 * Floating controls around the canvas (DESIGN.md 8.17). The grid itself
 * ignores the pointer, so empty areas pass input through to the canvas;
 * only the controls capture it.
 */
import BaseButton from '@focus/ui/components/ui/BaseButton.vue';
import { useMainStore } from '../store/main';
import { useUndoShortcuts } from '../composables/useUndoShortcuts';
import SampleBadge from './hud/SampleBadge.vue';
import ToolRail from './hud/ToolRail.vue';
import TransformBar from './hud/TransformBar.vue';
import LayerDock from './hud/LayerDock.vue';

const store = useMainStore();
useUndoShortcuts();
</script>

<template>
  <div class="hud-grid fixed inset-0 z-hud p-hud">
    <SampleBadge class="[grid-area:badge] self-start justify-self-center" />
    <ToolRail class="[grid-area:rail] self-center justify-self-start" />
    <LayerDock class="[grid-area:dock] self-end justify-self-start" />
    <TransformBar class="[grid-area:transform] self-end justify-self-center" />
    <BaseButton
      variant="primary"
      size="xl"
      icon="check"
      class="[grid-area:confirm] self-end justify-self-end"
      @click="store.confirm"
    >
      Confirm alignment
    </BaseButton>
  </div>
</template>
