/** Driver for the canvas-first workspace (floating HUD). Queries by accessible name. */
import { flushPromises, mount } from '@vue/test-utils';
import { vi } from 'vitest';
import type { Pinia } from 'pinia';
import AlignmentWorkspace from '../../src/workspace/AlignmentWorkspace.vue';
import App from '../../src/App.vue';
import { click, typeInto, type Driver, type Field, type Layer } from './driver';

const LABEL: Record<Field, string> = { scale: 'scale', rotation: 'rotation', zoom: 'zoom' };
const TITLE: Record<Field, string> = { scale: 'Scale', rotation: 'Rotation', zoom: 'Zoom' };

const q = <T extends Element = HTMLElement>(sel: string) => {
  const el = document.querySelector<T>(sel);
  if (!el) throw new Error(`driver: ${sel} not found`);
  return el;
};
const byText = (scope: ParentNode, sel: string, text: string) =>
  [...scope.querySelectorAll(sel)].find(b => b.textContent?.trim() === text);
const panel = (layer: Layer) => q(`[data-layer="${layer}"]`);

export function workspaceDriver(pinia: Pinia): Driver {
  const opts = { global: { plugins: [pinia] }, attachTo: document.body };
  return {
    name: 'workspace',
    mount: () => mount(AlignmentWorkspace, opts),
    mountApp: () => mount(App, opts),
    targetCanvas: () => [...document.querySelectorAll('canvas')].at(-1) as HTMLCanvasElement,
    async setMode(_w, mode) { click(q(`[role=radio][aria-label^="${mode === 'aligner' ? 'Aligner' : 'Camera'}"]`)); },
    async flip(_w, axis) { click(q(`button[aria-label="Flip ${axis === 'h' ? 'horizontally' : 'vertically'}"]`)); },
    async type(_w, f, value) { await openField(f); typeInto(q(`[role=group][aria-label="${TITLE[f]}"] input`), value); },
    async hold(_w, f, dir, ms) {
      await openField(f);
      const btn = q(`button[aria-label="${dir === '-' ? 'Decrease' : 'Increase'} ${LABEL[f]}"]`);
      btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      await vi.advanceTimersByTimeAsync(ms);
      btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    },
    async resetField(_w, f) { await openField(f); click(q(`button[aria-label="Reset ${LABEL[f]}"]`)); },
    async resetDistortion() { await openResets(); click(byText(document, '[role=menuitem]', 'Reset distortion')); },
    async resetTransform() { await openResets(); click(byText(document, '[role=menuitem]', 'Reset transform')); },
    async setOpacity(_w, value) { typeInto(panel('target').querySelector('input[type=range]')!, value); },
    async typeSpotSize(_w, layer, axis, value) {
      typeInto(panel(layer).querySelector(`input[aria-label="Spot ${axis === 0 ? 'width' : 'height'}"]`)!, value);
    },
    async toggleClass(_w, layer, cls) { click(panel(layer).querySelector(`[data-class="${cls}"]`)); },
    async allClasses(_w, layer) { click(byText(panel(layer), 'section[aria-label=Clusters] button', 'All')); },
    async noClasses(_w, layer) { click(byText(panel(layer), 'section[aria-label=Clusters] button', 'None')); },
    async foreground(_w, layer, mode) {
      const label = { all: 'All', foreground: 'Foreground', background: 'Background' }[mode];
      click(panel(layer).querySelector(`[role=radio][aria-label="${label}"]`));
    },
    async confirm() { click(byText(document, 'button', 'Confirm alignment')); },
  };
}

/** The transform island shows one editor at a time: close the open one, then open `f`'s. */
async function openField(f: Field) {
  if (document.querySelector(`[role=group][aria-label="${TITLE[f]}"]`)) return;
  if (document.querySelector('[role=group][aria-label]:is([aria-label=Scale],[aria-label=Rotation],[aria-label=Zoom])')) {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await flushPromises();
  }
  click(q(`button[data-field="${f}"]`));
  await flushPromises();
}

async function openResets() {
  click(q('button[aria-label="Reset options"]'));
  await vi.advanceTimersByTimeAsync(0);
  await Promise.resolve();
}
