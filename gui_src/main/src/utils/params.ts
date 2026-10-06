/** Helpers for schema-driven parameter forms (processing and registration). */
import type { Modality, ParamSpec } from '../api/types';

export type ParamSpecs = Record<string, ParamSpec>;

/**
 * How an emptied text field is stored.
 * - 'nullable': null when the spec is nullable, otherwise ''
 * - 'null':     always null
 */
export type EmptyTextPolicy = 'nullable' | 'null';

/** Default value for every parameter in a spec set. */
export function paramDefaults(specs: ParamSpecs | undefined): Record<string, unknown> {
  const defaults: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(specs ?? {})) {
    defaults[key] = spec.default;
  }
  return defaults;
}

// Text-like inputs first, then dropdowns, then toggles; alphabetical within a group.
const TYPE_ORDER: Record<ParamSpec['type'], number> = {
  string: 0, int: 0, float: 0, path: 0, enum: 1, bool: 2,
};

export function sortParamEntries(specs: ParamSpecs): [string, ParamSpec][] {
  return Object.entries(specs).sort(([keyA, a], [keyB, b]) => {
    const diff = TYPE_ORDER[a.type] - TYPE_ORDER[b.type];
    return diff !== 0 ? diff : keyA.localeCompare(keyB);
  });
}

/** Convert a raw input string to the stored value for a spec. */
export function parseParamValue(spec: ParamSpec, raw: string, emptyText: EmptyTextPolicy): unknown {
  if (spec.type === 'int' || spec.type === 'float') {
    if (raw === '' && spec.nullable) return null;
    return spec.type === 'int' ? parseInt(raw, 10) || 0 : parseFloat(raw) || 0;
  }
  if (raw) return raw;
  return emptyText === 'null' || spec.nullable ? null : '';
}

/** Parameter key of the force-recompute flag; it is edited on Review, not in the settings forms. */
export const FORCE_KEY = 'force_recomputing';

/** True when a modality carries settings that a type or reference change would discard. */
export function hasCustomSettings(m: Modality): boolean {
  const custom = Object.keys(m.processing_settings ?? {}).some(k => k !== FORCE_KEY);
  return custom || m.registration_type !== 'none' || m.alignment_strategy !== 'manual';
}
