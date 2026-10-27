import type { Metadata } from '../api/types';

// Warm palette: reds, oranges, yellows, pinks, magentas, warm grays
const REFERENCE_COLORS = [
  '#d94848', // red
  '#e07830', // burnt orange
  '#e0a820', // amber
  '#e8cc30', // golden yellow
  '#d43872', // deep pink
  '#e07888', // soft pink
  '#c03898', // magenta
  '#e89060', // peach
  '#c05040', // terracotta
  '#e8b098', // light peach
  '#a02848', // dark rose
  '#d8b840', // warm yellow
  '#f07878', // light coral
  '#b84030', // brick red
  '#ccc0b8', // warm light gray
  '#9a8070', // warm mid gray
];

// Cool palette: blues, purples, greens, teals, earthy browns, cool grays
const TARGET_COLORS = [
  '#3890d0', // sky blue
  '#3050c0', // cobalt
  '#5840c0', // indigo
  '#8030c0', // purple
  '#28a898', // teal
  '#30b8c8', // cyan
  '#389858', // forest green
  '#789028', // olive
  '#487038', // dark olive
  '#805840', // earthy brown
  '#487090', // slate blue
  '#509878', // sage green
  '#203080', // dark navy
  '#7088a8', // cool gray-blue
  '#289868', // emerald
  '#907858', // khaki
];

/** Observations below the clustering eligibility thresholds; matches focus.spot_colors. */
export const UNASSIGNED_COLOR = '#92979f';

function hslHex(h: number, s: number, l: number): string {
  const a = s * Math.min(l, 1 - l);
  const channel = (n: number) => {
    const k = (n + h / 30) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))).toString(16).padStart(2, '0');
  };
  return `#${channel(0)}${channel(8)}${channel(4)}`;
}

// Classes past the fixed palette continue in the same hue band (golden-ratio hue steps,
// alternating lightness), so colors never cycle and each layer keeps its warm/cool family.
const paletteFamily = (palette: string[], hueStart: number, hueSpan: number) => (cls: number): string => {
  if (cls < palette.length) return palette[cls]!;
  const k = cls - palette.length;
  const hue = (hueStart + ((k * 0.618033988749895) % 1) * hueSpan) % 360;
  return hslHex(hue, 0.6, [0.46, 0.62, 0.34][k % 3]!);
};

export const getReferenceColor = paletteFamily(REFERENCE_COLORS, 310, 100); // magenta to yellow
export const getTargetColor = paletteFamily(TARGET_COLORS, 100, 170); // green to violet

/** Shared by canvas marks and filter chips: the layer's family by class index, neutral for unassigned. */
export function classColor(meta: Metadata | null, cls: number, target: boolean): string {
  if (meta?.cluster_labels?.[cls] === 'unassigned') return UNASSIGNED_COLOR;
  return target ? getTargetColor(cls) : getReferenceColor(cls);
}
