/**
 * Intent driver: the user actions the golden tests perform, independent of
 * the DOM that implements them. Each GUI version provides one implementation.
 */
import type { VueWrapper } from '@vue/test-utils';

export type Field = 'scale' | 'rotation' | 'zoom';
export type Layer = 'target' | 'reference';
export type FgMode = 'all' | 'foreground' | 'background';

export interface Driver {
  name: string;
  mount(): VueWrapper;
  /** Mounts the whole app shell (screen switching, sample loading). */
  mountApp(): VueWrapper;
  targetCanvas(w: VueWrapper): HTMLCanvasElement;
  setMode(w: VueWrapper, mode: 'aligner' | 'camera'): Promise<void>;
  flip(w: VueWrapper, axis: 'h' | 'v'): Promise<void>;
  type(w: VueWrapper, field: Field, value: string): Promise<void>;
  hold(w: VueWrapper, field: Field, dir: '+' | '-', ms: number): Promise<void>;
  resetField(w: VueWrapper, field: Field): Promise<void>;
  resetDistortion(w: VueWrapper): Promise<void>;
  resetTransform(w: VueWrapper): Promise<void>;
  setOpacity(w: VueWrapper, value: string): Promise<void>;
  typeSpotSize(w: VueWrapper, layer: Layer, axis: 0 | 1, value: string): Promise<void>;
  toggleClass(w: VueWrapper, layer: Layer, cls: number): Promise<void>;
  allClasses(w: VueWrapper, layer: Layer): Promise<void>;
  noClasses(w: VueWrapper, layer: Layer): Promise<void>;
  foreground(w: VueWrapper, layer: Layer, mode: FgMode): Promise<void>;
  confirm(w: VueWrapper): Promise<void>;
}

export function typeInto(el: Element, value: string) {
  (el as HTMLInputElement).value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

export function click(el: Element | null | undefined) {
  if (!el) throw new Error('driver: element not found');
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
}
