/**
 * Force-recompute flags as the pipeline will actually apply them.
 *
 * Mirrors `_compute_effective_force_flags` in src/focus/orchestrator.py:
 * - reference preprocessing forced: every alignment and registration re-runs
 * - target preprocessing forced: that modality's alignment and registration re-run
 * - alignment forced (for any reason): that modality's registration re-runs
 * Keep the two in sync when the cascade changes.
 */
import type { Config, Modality } from '../api/types';
import type { ForceStage } from '../store/main';

export interface ForceCell {
  /** False when the stage does not run for this modality. */
  applicable: boolean;
  /** The flag stored in the config. */
  explicit: boolean;
  /** Whether the stage will be recomputed, including the cascade. */
  effective: boolean;
  /** Why the stage re-runs when it is not set explicitly. */
  reason: string;
}

export type ForceRow = Record<ForceStage, ForceCell> & { name: string; reference: boolean };

const isForced = (settings: Record<string, unknown> | undefined) => settings?.force_recomputing === true;

export function forceRows(config: Config): ForceRow[] {
  const ref = config.modalities.find(m => m.name === config.reference_modality);
  const refPre = isForced(ref?.processing_settings);
  const multi = config.modalities.length >= 2;

  return config.modalities.map((m: Modality): ForceRow => {
    const isRef = m.name === config.reference_modality;
    const pre = isForced(m.processing_settings);

    const alignApplicable = !isRef && multi && config.perform_alignment;
    const alignExplicit = m.alignment_force_recomputing === true;
    const alignReason = refPre
      ? `Reference (${config.reference_modality}) preprocessing is recomputed`
      : pre ? 'This modality\'s preprocessing is recomputed' : '';
    const alignEffective = alignExplicit || refPre || pre;

    const regApplicable = alignApplicable && config.perform_registration && m.registration_type !== 'none';
    const regExplicit = isForced(m.registration_settings);
    const regReason = alignEffective ? (alignExplicit ? 'Alignment is recomputed' : alignReason) : '';

    return {
      name: m.name,
      reference: isRef,
      preprocessing: { applicable: true, explicit: pre, effective: pre, reason: '' },
      alignment: { applicable: alignApplicable, explicit: alignExplicit, effective: alignEffective, reason: alignExplicit ? '' : alignReason },
      registration: { applicable: regApplicable, explicit: regExplicit, effective: regExplicit || alignEffective, reason: regExplicit ? '' : regReason },
    };
  });
}
