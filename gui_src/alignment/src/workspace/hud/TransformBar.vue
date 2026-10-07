<script setup lang="ts">
/**
 * Bottom-center transform island. Collapsed, it shows the layer scale and
 * rotation, then the view zoom, as values only; pressing one swaps the
 * island to that value's stepper, which stays open until a press outside
 * the island or Escape.
 */
import { computed, ref, watch, type WritableComputedRef } from 'vue';
import ChromeSurface from '@focus/ui/components/ui/ChromeSurface.vue';
import NumberStepper from '@focus/ui/components/ui/NumberStepper.vue';
import Separator from '@focus/ui/components/ui/Separator.vue';
import { useDismiss } from '@focus/ui/composables/useDismiss';
import { useTransformControls } from '../../composables/useTransformControls';
import TransformValue from './TransformValue.vue';

const c = useTransformControls();

interface Field {
  id: 'scale' | 'rotation' | 'zoom';
  label: string;
  unit?: string;
  step: string;
  /** Decimals of the collapsed readout (display only). */
  digits: number;
  model: WritableComputedRef<number>;
  onStep: (direction: -1 | 1) => void;
  onReset: () => void;
}

const FIELDS: Field[] = [
  { id: 'scale', label: 'Scale', step: '0.01', digits: 3, model: c.scale, onStep: c.stepScale, onReset: c.resetScale },
  { id: 'rotation', label: 'Rotation', unit: '°', step: '1', digits: 1, model: c.rotation, onStep: c.stepRotation, onReset: c.resetRotation },
  { id: 'zoom', label: 'Zoom', unit: '×', step: '0.1', digits: 2, model: c.zoom, onStep: c.stepZoom, onReset: c.resetZoom },
];

const openId = ref<Field['id'] | null>(null);
const active = computed(() => FIELDS.find(f => f.id === openId.value) ?? null);

const root = ref<HTMLElement | null>(null);
const editor = ref<InstanceType<typeof NumberStepper> | null>(null);
useDismiss([root], computed(() => openId.value !== null), () => { openId.value = null; });
watch(editor, e => e?.focus());

const readout = (f: Field) => Number(f.model.value).toFixed(f.digits);
</script>

<template>
  <div ref="root">
    <ChromeSurface aria-label="Transform">
      <Transition name="island" mode="out-in">
        <div v-if="active" :key="active.id" class="flex origin-bottom items-center">
          <NumberStepper
            ref="editor"
            :model-value="active.model.value"
            :label="active.label"
            :unit="active.unit"
            :step="active.step"
            :reset-label="`Reset ${active.label.toLowerCase()}`"
            @update:model-value="v => (active!.model.value = v as number)"
            @step="active.onStep"
            @reset="active.onReset"
          />
        </div>
        <div v-else class="flex items-center gap-1">
          <template v-for="f in FIELDS" :key="f.id">
            <Separator v-if="f.id === 'zoom'" />
            <TransformValue :label="f.label" :value="readout(f)" :unit="f.unit" :data-field="f.id" @open="openId = f.id" />
          </template>
        </div>
      </Transition>
    </ChromeSurface>
  </div>
</template>
