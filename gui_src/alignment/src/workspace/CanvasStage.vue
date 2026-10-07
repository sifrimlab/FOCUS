<script setup lang="ts">
/**
 * Full-viewport canvas on the neutral ground: the fixed layer below, the
 * moving layer (which takes all pointer input) above. The stage always
 * fills the window, so floating controls never resize the canvas.
 */
import ReferenceCanvas from '../canvas/ReferenceCanvas.vue';
import TargetCanvas from '../canvas/TargetCanvas.vue';
</script>

<template>
  <div class="fixed inset-0 overflow-hidden bg-canvas-ground">
    <ReferenceCanvas />
    <TargetCanvas />
  </div>
</template>
