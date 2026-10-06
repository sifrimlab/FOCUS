/** Text formatting helpers shared across views. */

/** Last path segment: "/a/b/file.h5ad" -> "file.h5ad". */
export function basename(path: string): string {
  return path.split('/').pop() || path;
}

/** Schema key to label: "min_counts_per_cell" -> "Min counts per cell". */
export function humanizeKey(key: string): string {
  const words = key.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "1 sample", "3 samples". */
export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
