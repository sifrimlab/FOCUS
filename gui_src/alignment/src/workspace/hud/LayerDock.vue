<script setup lang="ts">
/** Bottom-left stack of the two layer panels: moving layer above the fixed one. */
import LayerPanel from './LayerPanel.vue';
</script>

<template>
  <div class="flex flex-col gap-2">
    <LayerPanel role="target" />
    <LayerPanel role="reference" />
  </div>
</template>
