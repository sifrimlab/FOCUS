<script setup lang="ts">
/**
 * Force-recompute switches per modality and stage. Stages that do not run
 * show a dash; stages that re-run because of an upstream switch show as on,
 * dimmed and locked, with the reason as a tooltip.
 */
import { computed } from 'vue';
import { useMainStore, type ForceStage } from '../../../store/main';
import { forceRows, type ForceCell } from '../../../utils/forceFlags';
import ToggleSwitch from '../../ui/ToggleSwitch.vue';
import AppIcon from '../../ui/AppIcon.vue';

const store = useMainStore();

const STAGES: { id: ForceStage; label: string }[] = [
  { id: 'preprocessing', label: 'Preprocessing' },
  { id: 'alignment', label: 'Alignment' },
  { id: 'registration', label: 'Registration' },
];

const rows = computed(() => forceRows(store.config));
const columns = computed(() => STAGES.filter(s => rows.value.some(r => r[s.id].applicable)));

const implied = (cell: ForceCell) => cell.effective && !cell.explicit;
const label = (name: string, stage: string) => `Force recompute ${stage.toLowerCase()} of ${name}`;
</script>

<template>
  <div class="flex flex-col gap-3">
    <table class="w-full">
      <thead>
        <tr class="type-footnote text-fg3 text-left">
          <th scope="col" class="pb-2 font-normal">Modality</th>
          <th v-for="c in columns" :key="c.id" scope="col" class="w-32 pb-2 text-center font-normal">{{ c.label }}</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-separator border-t border-separator">
        <tr v-for="(row, i) in rows" :key="row.name">
          <th scope="row" class="py-2.5 text-left font-normal">
            <span class="type-body-strong text-fg1 inline-flex items-center gap-1.5">
              <AppIcon v-if="row.reference" name="star" :size="12" class="text-warning" />
              {{ row.name }}
            </span>
          </th>
          <td v-for="c in columns" :key="c.id" class="py-2.5 text-center">
            <span v-if="!row[c.id].applicable" class="type-callout text-fg4" aria-label="Not applicable">—</span>
            <span v-else-if="implied(row[c.id])" class="inline-flex" :title="`Re-runs: ${row[c.id].reason}`">
              <ToggleSwitch tone="warning" :model-value="true" disabled :label="`${label(row.name, c.label)} (implied: ${row[c.id].reason})`" />
            </span>
            <ToggleSwitch
              v-else
              tone="warning"
              class="inline-block"
              :model-value="row[c.id].explicit"
              :label="label(row.name, c.label)"
              @update:model-value="store.setForceFlag(i, c.id, $event)"
            />
          </td>
        </tr>
      </tbody>
    </table>
    <p class="type-footnote text-fg3">
      Forced stages ignore cached results and run again. Forcing a stage also re-runs the stages that depend on it;
      those appear dimmed.
    </p>
  </div>
</template>
