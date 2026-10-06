/**
 * View model of a running pipeline for the progress screen.
 *
 * Combines three sources: the planned stages (from the config), the live
 * position (pipeline status), and the run history (status.timeline from the
 * backend ProgressTracker). Components only render what this returns.
 */
import { computed, inject, provide, type InjectionKey } from 'vue';
import { useMainStore } from '../store/main';
import { plannedStages, type StageId } from '../utils/runPlan';
import type { ModalityRecord, StageRecord } from '../api/types';
import { useServerClock } from './useServerClock';

export type ProgressState = 'done' | 'active' | 'pending' | 'failed';

export interface ModalityProgress {
  name: string;
  state: ProgressState;
  seconds: number | null;
}

export interface StageProgress {
  id: StageId;
  label: string;
  state: ProgressState;
  seconds: number | null;
  modalities: ModalityProgress[];
}

export type SampleState = 'done' | 'current' | 'pending';

const STEP_PREFIX = /^\d+(?:-\d+)?\/\d+\s*[-–]\s*/;

export function useRunProgress() {
  const store = useMainStore();
  const status = computed(() => store.pipelineStatus);
  const { elapsed } = useServerClock(computed(() => status.value.server_now));

  const finished = computed(() => status.value.state === 'completed' || status.value.state === 'error');
  const records = computed(() => new Map<string, StageRecord>(status.value.timeline.map(r => [r.stage, r])));

  const stateOf = (record: { ended_at: number | null } | undefined, failed = false): ProgressState => {
    if (failed) return 'failed';
    if (!record) return 'pending';
    return record.ended_at ? 'done' : 'active';
  };

  const stages = computed<StageProgress[]>(() => {
    const plan = plannedStages(store.config);
    // A stage with no record that the run has already moved past was skipped by the pipeline: show it as done.
    const lastStarted = plan.reduce((last, p, i) => (records.value.has(p.id) ? i : last), -1);
    return plan.map((plan, i) => {
      const rec = records.value.get(plan.id);
      const skipped = !rec && (i < lastStarted || status.value.state === 'completed');
      return {
        id: plan.id,
        label: plan.label,
        state: skipped ? 'done' : stateOf(rec, rec?.failed),
        seconds: rec ? elapsed(rec.started_at, rec.ended_at) : null,
        modalities: plan.modalities.map(name => {
          const m = rec?.modalities.find(r => r.name === name);
          const state: ProgressState = m ? stateOf(m) : rec?.ended_at ? 'done' : 'pending';
          return { name, state, seconds: m ? elapsed(m.started_at, m.ended_at) : null };
        }),
      };
    });
  });

  /** The stage the run is in now (or failed in). */
  const activeStage = computed(() => stages.value.find(s => s.state === 'active' || s.state === 'failed') ?? null);
  const activeRecord = computed(() => (activeStage.value ? records.value.get(activeStage.value.id) : undefined));
  const activeModality = computed<ModalityRecord | undefined>(() => {
    const mods = activeRecord.value?.modalities ?? [];
    const last = mods[mods.length - 1];
    return last && last.name === status.value.current_modality ? last : undefined;
  });

  const runSeconds = computed(() => {
    const end = finished.value ? Math.max(0, ...status.value.timeline.map(r => r.ended_at ?? 0)) || null : null;
    return elapsed(status.value.run_started_at, end);
  });

  /** Step label without its "3/8 - " prefix. */
  const stepLabel = computed(() => (status.value.sub_step ?? '').replace(STEP_PREFIX, ''));

  /** Samples included in the run, in dataset order. */
  const includedSamples = computed(() => store.samples.filter(id => !store.config.ignore_samples.includes(id)));

  /** Done / current / pending per included sample, for the current modality (or the stage itself). */
  const sampleStates = computed<Record<string, SampleState>>(() => {
    const s = status.value;
    const seen = activeModality.value?.samples ?? activeRecord.value?.samples ?? [];
    // In a per-sample step (unit "sample"), the first `progress` samples of this pass are done.
    const done = new Set(s.sub_step_unit === 'sample' ? seen.slice(0, s.sub_step_progress) : seen);
    if (s.current_sample) done.delete(s.current_sample);
    const result: Record<string, SampleState> = {};
    for (const id of includedSamples.value) {
      result[id] = id === s.current_sample ? 'current' : done.has(id) ? 'done' : 'pending';
    }
    return result;
  });

  return {
    status,
    finished,
    stages,
    activeStage,
    activeModality,
    runSeconds,
    stepLabel,
    includedSamples,
    sampleStates,
    elapsed,
  };
}

export type RunProgress = ReturnType<typeof useRunProgress>;

const RUN_PROGRESS: InjectionKey<RunProgress> = Symbol('run-progress');

/** Create the view model once (one clock) and share it with the progress components. */
export function provideRunProgress(): RunProgress {
  const run = useRunProgress();
  provide(RUN_PROGRESS, run);
  return run;
}

export function injectRunProgress(): RunProgress {
  const run = inject(RUN_PROGRESS);
  if (!run) throw new Error('injectRunProgress() needs provideRunProgress() in an ancestor');
  return run;
}
