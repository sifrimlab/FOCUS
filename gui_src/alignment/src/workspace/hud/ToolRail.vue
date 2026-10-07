<script setup lang="ts">
/**
 * Left-edge tool rail: undo and redo, interaction mode, flips, and (behind
 * a menu, as they are rarely needed) the two resets.
 */
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import SegmentedControl, { type Segment } from '@focus/ui/components/ui/SegmentedControl.vue';
import IconButton from '@focus/ui/components/ui/IconButton.vue';
import OverflowMenu, { type MenuItem } from '@focus/ui/components/ui/OverflowMenu.vue';
import Separator from '@focus/ui/components/ui/Separator.vue';
import { useUiStore } from '../../store/ui';
import { useTransformControls } from '../../composables/useTransformControls';

const ui = useUiStore();
const { mode, flip, resetDistortion, resetTransform, undo, redo } = useTransformControls();

const MODES: Segment<'aligner' | 'camera'>[] = [
  { value: 'aligner', label: 'Aligner: drag, distort and rotate the moving layer', icon: 'cursor-arrow-rays' },
  { value: 'camera', label: 'Camera: pan and zoom the view', icon: 'hand-raised' },
];

const RESETS: MenuItem[] = [
  { label: 'Reset distortion', icon: 'arrow-path', action: resetDistortion },
  { label: 'Reset transform', icon: 'arrow-path-rounded-square', action: resetTransform },
];
</script>

<template>
  <ChromeSurface as="nav" class="flex flex-col items-center gap-1" aria-label="Tools">
    <IconButton icon="arrow-uturn-left" label="Undo" size="lg" round :disabled="!ui.canUndo" @click="undo" />
    <IconButton icon="arrow-uturn-right" label="Redo" size="lg" round :disabled="!ui.canRedo" @click="redo" />
    <Separator orientation="horizontal" />
    <SegmentedControl v-model="mode" :segments="MODES" label="Interaction mode" orientation="vertical" size="lg" icon-only />
    <Separator orientation="horizontal" />
    <IconButton icon="arrows-right-left" label="Flip horizontally" size="lg" round @click="flip('horizontal')" />
    <IconButton icon="arrows-up-down" label="Flip vertically" size="lg" round @click="flip('vertical')" />
    <Separator orientation="horizontal" />
    <OverflowMenu :items="RESETS" label="Reset options" placement="right-end" size="lg" />
  </ChromeSurface>
</template>
