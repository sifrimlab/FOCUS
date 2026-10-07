/** Cluster visibility of a spot layer: toggle one class, show all, show none. */
import type { Layer } from './useLayer';

export function useClassFilter(layer: Layer) {
  const isShown = (cls: number) => layer.classFilter.includes(cls);

  const toggle = (cls: number) => {
    const idx = layer.classFilter.indexOf(cls);
    if (idx === -1) {
      layer.classFilter = [...layer.classFilter, cls];
    } else {
      layer.classFilter = layer.classFilter.filter(c => c !== cls);
    }
  };

  const showAll = () => { layer.classFilter = [...layer.classes]; };
  const showNone = () => { layer.classFilter = []; };

  return { isShown, toggle, showAll, showNone };
}
