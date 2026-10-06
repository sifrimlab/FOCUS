/**
 * The stages a run will go through for a configuration, and the modalities
 * each stage processes. Mirrors the stage gating in src/focus/orchestrator.py
 * (run()); keep the two in sync.
 */
import type { Config, Modality } from '../api/types';

export type StageId = 'preprocessing' | 'alignment' | 'annotation_transfer' | 'registration' | 'compiling';

/** Stage names; mirror STAGE_LABELS in src/focus/reporting.py. */
export const STAGE_LABELS: Record<StageId, string> = {
  preprocessing: 'Preprocessing',
  alignment: 'Alignment',
  annotation_transfer: 'Annotation transfer',
  registration: 'Registration',
  compiling: 'Compiling',
};

export interface PlannedStage {
  id: StageId;
  label: string;
  /** Modalities processed one after another in this stage; empty when the stage has no modality level. */
  modalities: string[];
}

/** Modality types whose reference allows building the multimodal dataset (SPOT_MODALITY_TYPES). */
const SPOT_TYPES = ['msi', 'st'];

export function plannedStages(cfg: Config): PlannedStage[] {
  const names = (list: Modality[]) => list.map(m => m.name);
  const targets = cfg.modalities.filter(m => m.name !== cfg.reference_modality);
  const reference = cfg.modalities.find(m => m.name === cfg.reference_modality);

  const stages: PlannedStage[] = [{ id: 'preprocessing', label: STAGE_LABELS.preprocessing, modalities: names(cfg.modalities) }];
  if (cfg.perform_alignment && cfg.modalities.length >= 2) {
    stages.push({ id: 'alignment', label: STAGE_LABELS.alignment, modalities: names(targets) });
  }
  if (cfg.spatial_annotations !== null) {
    stages.push({ id: 'annotation_transfer', label: STAGE_LABELS.annotation_transfer, modalities: [] });
  }
  if (cfg.perform_registration) {
    stages.push({
      id: 'registration',
      label: STAGE_LABELS.registration,
      modalities: names(targets.filter(m => m.registration_type !== 'none')),
    });
    if (reference && SPOT_TYPES.includes(reference.type)) {
      stages.push({ id: 'compiling', label: STAGE_LABELS.compiling, modalities: [] });
    }
  }
  return stages;
}
