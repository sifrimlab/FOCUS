export const getReferenceColor = (cls: number) => {
  const hue = (cls * 137.508) % 360;
  return `hsl(${hue}, 70%, 50%)`;
};

export const getTargetColor = (cls: number) => {
  const hue = (cls * 137.508 + 180) % 360;
  return `hsl(${hue}, 70%, 50%)`;
};
