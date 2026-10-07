<script setup lang="ts">
/**
 * Left-edge tool rail: interaction mode, flips, and (behind a menu, as they
 * are rarely needed) the two resets.
 */
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import SegmentedControl, { type Segment } from '@focus/ui/components/ui/SegmentedControl.vue';
import IconButton from '@focus/ui/components/ui/IconButton.vue';
import OverflowMenu, { type MenuItem } from '@focus/ui/components/ui/OverflowMenu.vue';
import Separator from '@focus/ui/components/ui/Separator.vue';
import { useTransformControls } from '../../composables/useTransformControls';

const { mode, flip, resetDistortion, resetTransform } = useTransformControls();

const MODES: Segment<'aligner' | 'camera'>[] = [
  { value: 'aligner', label: 'Aligner: drag, distort and rotate the moving layer', icon: 'cursor-arrow-rays' },
  { value: 'camera', label: 'Camera: pan and zoom the view', icon: 'hand-raised' },
];

const RESETS: MenuItem[] = [
  { label: 'Reset distortion', icon: 'arrow-uturn-left', action: resetDistortion },
  { label: 'Reset transform', icon: 'arrow-path', action: resetTransform },
];
</script>

<template>
  <ChromeSurface as="nav" class="flex flex-col items-center gap-1" aria-label="Tools">
    <SegmentedControl v-model="mode" :segments="MODES" label="Interaction mode" orientation="vertical" icon-only />
    <Separator orientation="horizontal" />
    <IconButton icon="arrows-right-left" label="Flip horizontally" round @click="flip('horizontal')" />
    <IconButton icon="arrows-up-down" label="Flip vertically" round @click="flip('vertical')" />
    <Separator orientation="horizontal" />
    <OverflowMenu :items="RESETS" label="Reset options" placement="right-end" />
  </ChromeSurface>
</template>
