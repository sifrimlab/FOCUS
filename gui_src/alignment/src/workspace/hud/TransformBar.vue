<script setup lang="ts">
/** Bottom-center numeric controls: layer scale and rotation, then the view zoom. */
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import NumberStepper from '@focus/ui/components/ui/NumberStepper.vue';
import Separator from '@focus/ui/components/ui/Separator.vue';
import { useTransformControls } from '../../composables/useTransformControls';

const { scale, rotation, zoom, stepScale, stepRotation, stepZoom, resetScale, resetRotation, resetZoom } = useTransformControls();
</script>

<template>
  <ChromeSurface class="flex items-center gap-1" aria-label="Transform">
    <NumberStepper v-model="scale" label="Scale" step="0.01" reset-label="Reset scale" @step="stepScale" @reset="resetScale" />
    <NumberStepper v-model="rotation" label="Rotation" unit="°" step="1" reset-label="Reset rotation" @step="stepRotation" @reset="resetRotation" />
    <Separator />
    <NumberStepper v-model="zoom" label="Zoom" unit="×" step="0.1" reset-label="Reset zoom" @step="stepZoom" @reset="resetZoom" />
  </ChromeSurface>
</template>
