<script setup lang="ts">
/**
 * Left-edge tool rail: undo and redo, interaction mode (aligner, camera and,
 * for a spot moving layer, landmarks and exclusions), flips, and (behind a
 * menu, as they are rarely needed) the two resets.
 */
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import SegmentedControl, { type Segment } from '@focus/ui/components/ui/SegmentedControl.vue';
import IconButton from '@focus/ui/components/ui/IconButton.vue';
import OverflowMenu, { type MenuItem } from '@focus/ui/components/ui/OverflowMenu.vue';
import Separator from '@focus/ui/components/ui/Separator.vue';
import { computed } from 'vue';
import { useUiStore } from '../../store/ui';
import { useMainStore, type ControlMode } from '../../store/main';
import { useTransformControls } from '../../composables/useTransformControls';

const ui = useUiStore();
const store = useMainStore();
const { mode, flip, resetDistortion, resetTransform, undo, redo } = useTransformControls();

const BASE_MODES: Segment<ControlMode>[] = [
  { value: 'aligner', label: 'Aligner: drag, distort and rotate the moving layer', icon: 'cursor-arrow-rays' },
  { value: 'camera', label: 'Camera: pan and zoom the view', icon: 'hand-raised' },
];
// Landmarks warp a spot layer; an image moving layer keeps the matrix tools only.
const WARP_MODES: Segment<ControlMode>[] = [
  { value: 'landmark', label: 'Landmarks: pin a structure and drag it onto its counterpart', icon: 'map-pin' },
  { value: 'exclude', label: 'Exclude: lasso tissue with no counterpart (tears, folds, missing pieces)', icon: 'no-symbol' },
];
const modes = computed(() =>
  store.targetMeta?.modality_type === 'SPOT' ? [...BASE_MODES, ...WARP_MODES] : BASE_MODES);

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
    <SegmentedControl v-model="mode" :segments="modes" label="Interaction mode" orientation="vertical" size="lg" icon-only />
    <Separator orientation="horizontal" />
    <IconButton icon="arrows-right-left" label="Flip horizontally" size="lg" round @click="flip('horizontal')" />
    <IconButton icon="arrows-up-down" label="Flip vertically" size="lg" round @click="flip('vertical')" />
    <Separator orientation="horizontal" />
    <OverflowMenu :items="RESETS" label="Reset options" placement="right-end" size="lg" />
  </ChromeSurface>
</template>
