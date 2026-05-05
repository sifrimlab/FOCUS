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

export const getReferenceColor = (cls: number): string =>
  REFERENCE_COLORS[((cls % REFERENCE_COLORS.length) + REFERENCE_COLORS.length) % REFERENCE_COLORS.length]!;

export const getTargetColor = (cls: number): string =>
  TARGET_COLORS[((cls % TARGET_COLORS.length) + TARGET_COLORS.length) % TARGET_COLORS.length]!;
