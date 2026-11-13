/**
 * How a spot layer is coloured: as clusters at a resolution the operator sets
 * at runtime, or as one feature image (an ion or a gene).
 *
 * Preprocessing overclusters the spots and stores the order of the Ward merges
 * of touching clusters (`meta.cluster_tree`); the classes of the payload are
 * the fine clusters. A resolution of L clusters applies the first n_fine - L
 * merges. Feature images arrive per spot in an FSP v2 payload (0 = no value).
 *
 * Both views reduce to one display key per spot with a colour per key, so the
 * per-key meshes, the class filter and culling work unchanged.
 */
import type { Metadata, SpotModalityPayload } from '../api/types';
import { getReferenceColor, getTargetColor, UNASSIGNED_COLOR } from './colors';

export type SpotViewMode = 'clusters' | 'features';
export interface SpotView { mode: SpotViewMode; level: number; feature: number }

export interface SpotDisplay {
  /** Display key of every spot, in payload order. */
  keys: Uint32Array;
  /** Colour and label of every key. */
  colors: string[];
  labels: string[];
  /** Keys can be hidden one by one (clusters); a feature image is continuous. */
  filterable: boolean;
}

/** Intensity levels of a feature image (one mesh group each), plus one key for "no value". */
export const FEATURE_LEVELS = 32;

// Magma, 9 stops (perceptually uniform, readable on light and dark grounds).
const MAGMA: [number, number, number][] = [
  [0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122],
  [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191],
];

/** Magma colour of t in [0, 1], as rgb(). */
export function magma(t: number): string {
  const x = Math.min(Math.max(t, 0), 1) * (MAGMA.length - 1);
  const i = Math.min(Math.floor(x), MAGMA.length - 2);
  const f = x - i;
  const [a, b] = [MAGMA[i]!, MAGMA[i + 1]!];
  const c = a.map((v, k) => Math.round(v + (b[k]! - v) * f));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/** The merge tree of a layer in class indices, or null without one. */
export function treeOf(meta: Metadata | null) {
  const labels = meta?.cluster_labels;
  if (!meta?.cluster_tree || !labels) return null;
  const index = new Map(labels.map((label, i) => [label, i]));
  const merges: [number, number][] = [];
  for (const [a, b] of meta.cluster_tree.merges) {
    const ia = index.get(a), ib = index.get(b);
    if (ia !== undefined && ib !== undefined) merges.push([ia, ib]);
  }
  const nFine = labels.filter(l => l !== 'unassigned').length;
  const defaultLevel = Math.min(Math.max(meta.cluster_tree.default_level, 1), nFine);
  return { merges, nFine, defaultLevel };
}

/** Starting view of a layer: clusters at the default resolution; null without a tree. */
export function initialView(meta: Metadata | null): SpotView | null {
  const tree = treeOf(meta);
  return tree ? { mode: 'clusters', level: tree.defaultLevel, feature: 0 } : null;
}

const classesOf = (data: SpotModalityPayload): Uint32Array =>
  data.classes ?? Uint32Array.from(data, s => s.class);

function clusterDisplay(meta: Metadata, data: SpotModalityPayload, level: number, target: boolean): SpotDisplay {
  const tree = treeOf(meta)!;
  const labels = meta.cluster_labels!;
  const root = Int32Array.from(labels, (_, i) => i);
  for (const [a, b] of tree.merges.slice(0, Math.max(0, tree.nFine - level))) {
    for (let i = 0; i < root.length; i++) if (root[i] === b) root[i] = a;
  }
  const classes = classesOf(data);
  const size = new Float64Array(labels.length);
  for (let i = 0; i < classes.length; i++) size[root[classes[i]!]!]! += 1;
  // Groups ranked by size (ties by class), so colours follow the largest groups.
  const roots = [...new Set(Array.from(root).filter((_, c) => labels[c] !== 'unassigned'))];
  roots.sort((a, b) => size[b]! - size[a]! || a - b);
  const rank = new Map(roots.map((r, k) => [r, k]));
  const unassigned = roots.length;
  const keyOfClass = Uint32Array.from(labels, (label, c) =>
    label === 'unassigned' ? unassigned : rank.get(root[c]!)!);
  const keys = new Uint32Array(classes.length);
  for (let i = 0; i < classes.length; i++) keys[i] = keyOfClass[classes[i]!]!;
  const color = target ? getTargetColor : getReferenceColor;
  return {
    keys,
    colors: [...roots.map((_, k) => color(k)), UNASSIGNED_COLOR],
    labels: [...roots.map((_, k) => String(k)), 'unassigned'],
    filterable: true,
  };
}

function featureDisplay(data: SpotModalityPayload, feature: number): SpotDisplay {
  const n = data.length;
  const width = data.nFeatures ?? 0;
  const values = data.features;
  const keys = new Uint32Array(n);
  for (let i = 0; i < n; i++) {
    const v = values && feature < width ? values[i * width + feature]! : 0;
    keys[i] = v === 0 ? FEATURE_LEVELS : Math.min(FEATURE_LEVELS - 1, Math.floor(((v - 1) * FEATURE_LEVELS) / 255));
  }
  return {
    keys,
    colors: [...Array.from({ length: FEATURE_LEVELS }, (_, k) => magma(k / (FEATURE_LEVELS - 1))), UNASSIGNED_COLOR],
    labels: [...Array.from({ length: FEATURE_LEVELS }, (_, k) => String(k)), 'no value'],
    filterable: false,
  };
}

/** Display keys of a layer under `view`; null keeps the plain per-class colouring. */
export function spotDisplay(
  meta: Metadata | null, data: SpotModalityPayload | null, view: SpotView | null, target: boolean,
): SpotDisplay | null {
  if (!meta || !data || !view || meta.modality_type !== 'SPOT') return null;
  if (view.mode === 'features' && data.nFeatures) return featureDisplay(data, view.feature);
  return treeOf(meta) ? clusterDisplay(meta, data, view.level, target) : null;
}
