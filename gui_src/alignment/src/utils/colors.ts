export const getReferenceColor = (cls: number) => {
  const hue = ((cls * 137.508) % 360) / 360 * 75;  // 0–75° warm (reds, oranges, yellows)
  return `hsl(${hue}, 60%, 58%)`;
};

export const getTargetColor = (cls: number) => {
  const hue = ((cls * 137.508) % 360) / 360 * 75 + 185;  // 185–260° cool (teals, blues, indigos)
  return `hsl(${hue}, 60%, 58%)`;
};
