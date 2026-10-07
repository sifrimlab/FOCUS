/**
 * Undo and redo of the moving layer: the history ring itself, one entry per
 * gesture, exact restores (including the "Reset distortion" target), the
 * redo branch, capacity, resize, shortcuts and the collapsible islands.
 */
import { describe, it, expect } from 'vitest';
import { vi } from 'vitest';
import { mat3 } from 'gl-matrix';
import { PAIRS } from './harness/fixtures';
import { drag, openSession, settle, targetCornersOnScreen, triggerResize, type Ctx } from './harness/session';
import { matHex } from './harness/serialize';
import { click } from './harness/driver';
import { makeDriver } from './currentDriver';
import { createTransformHistory, HISTORY_CHANGES } from '../src/canvas/transformHistory';
import { SETTLE_MS } from '../src/canvas/useTransformHistory';
import { useUiStore } from '../src/store/ui';
import { useTransformControls } from '../src/composables/useTransformControls';
import { mount } from '@vue/test-utils';
import ChromeCluster from '@focus/ui/components/shell/ChromeCluster.vue';

const SIZE = { width: 1000, height: 700 };
const PAIR = PAIRS.find(p => p.tgt.meta.modality_type === 'SPOT') ?? PAIRS[0]!;

const translation = (x: number) => mat3.fromTranslation(mat3.create(), [x, 0]);

describe('transform history ring', () => {
  const snap = (x: number) => ({ transform: translation(x), distortBefore: null });
  const tx = (s: { transform: mat3 } | null) => s?.transform[6];

  it('undoes at most HISTORY_CHANGES changes, oldest dropped first', () => {
    const h = createTransformHistory();
    h.reset(snap(0));
    for (let i = 1; i <= HISTORY_CHANGES + 3; i++) h.push(snap(i));
    const seen: number[] = [];
    while (h.canUndo) seen.push(tx(h.undo())!);
    expect(seen).toHaveLength(HISTORY_CHANGES);
    expect(seen.at(-1)).toBe(3);
    expect(h.undo()).toBeNull();
  });

  it('a change after an undo cuts off the redo branch', () => {
    const h = createTransformHistory();
    h.reset(snap(0));
    h.push(snap(1));
    h.push(snap(2));
    h.undo();
    expect(h.canRedo).toBe(true);
    h.push(snap(5));
    expect(h.canRedo).toBe(false);
    expect(tx(h.undo())).toBe(1);
    expect(tx(h.redo())).toBe(5);
  });

  it('stores copies and rewrites every state on map', () => {
    const h = createTransformHistory();
    const m = translation(1);
    h.reset({ transform: m, distortBefore: m });
    m[6] = 99;
    h.push(snap(2));
    h.map(x => mat3.multiply(mat3.create(), translation(10), x));
    const back = h.undo()!;
    expect(back.transform[6]).toBe(11);
    expect(back.distortBefore![6]).toBe(11);
  });
});

async function withSession(fn: (c: Ctx) => Promise<void>, pair = PAIR) {
  const s = await openSession(await makeDriver(), pair, SIZE);
  try { await fn(s.ctx); } finally { s.close(); }
}

/** A browser press around an interaction: the history groups a gesture between pointerdown and pointerup. */
async function gesture(run: () => Promise<void> | void) {
  window.dispatchEvent(new Event('pointerdown'));
  await run();
  await settle();
  window.dispatchEvent(new Event('pointerup'));
  await commit();
}

const commit = async () => {
  await vi.advanceTimersByTimeAsync(SETTLE_MS + 10);
  await settle();
};

const undo = async () => { useTransformControls().undo(); await settle(); };
const redo = async () => { useTransformControls().redo(); await settle(); };

/** Counts the undo steps available, then redoes them all, leaving the state as found. */
async function undoDepth() {
  const ui = useUiStore();
  let n = 0;
  while (ui.canUndo) { await undo(); n++; }
  for (let i = 0; i < n; i++) await redo();
  return n;
}

describe('undo and redo in the workspace', () => {
  it('a drag is one change, and undo restores the fit byte for byte', () => withSession(async c => {
    const fit = matHex(c.store.targetTransform);
    const cx = SIZE.width / 2; const cy = SIZE.height / 2;
    await gesture(() => drag(c, [[cx, cy], [cx + 10, cy], [cx + 20, cy + 5], [cx + 40, cy + 10]]));
    expect(matHex(c.store.targetTransform)).not.toBe(fit);
    expect(await undoDepth()).toBe(1);

    await undo();
    expect(matHex(c.store.targetTransform)).toBe(fit);
    expect(useUiStore().canUndo).toBe(false);
    expect(useUiStore().canRedo).toBe(true);
  }));

  it('a held step button is one change', () => withSession(async c => {
    await gesture(() => c.d.hold(c.w, 'rotation', '+', 2000));
    expect(await undoDepth()).toBe(1);
  }));

  it('undo restores the "Reset distortion" target of that state', () => withSession(async c => {
    const fit = matHex(c.store.targetTransform);
    await gesture(() => c.d.type(c.w, 'scale', '1.5'));
    // A press on the canvas moves focus out of the field, as in the browser.
    (document.activeElement as HTMLElement | null)?.blur();
    await commit();
    const [x, y] = targetCornersOnScreen(c)[0]!;
    await gesture(() => drag(c, [[x, y], [x + 30, y - 20]]));
    expect(await undoDepth()).toBe(2);

    await undo();
    await c.d.resetDistortion(c.w);
    await settle();
    // The scaled state was recorded before any distortion, when the target was still the fit.
    expect(matHex(c.store.targetTransform)).toBe(fit);
  }));

  it('redo re-applies, and a new change clears redo', () => withSession(async c => {
    await gesture(() => c.d.flip(c.w, 'h'));
    const flipped = matHex(c.store.targetTransform);
    await undo();
    await redo();
    expect(matHex(c.store.targetTransform)).toBe(flipped);

    await undo();
    await gesture(() => c.d.flip(c.w, 'v'));
    expect(useUiStore().canRedo).toBe(false);
  }));

  it(`keeps the last ${HISTORY_CHANGES} changes`, () => withSession(async c => {
    for (let i = 0; i < HISTORY_CHANGES + 2; i++) await gesture(() => c.d.flip(c.w, i % 2 ? 'v' : 'h'));
    expect(await undoDepth()).toBe(HISTORY_CHANGES);
  }));

  it('undo after a resize keeps the layers aligned', () => withSession(async c => {
    const mapping = () => {
      const inv = mat3.invert(mat3.create(), c.store.referenceTransform)!;
      return Array.from(mat3.multiply(mat3.create(), inv, c.store.targetTransform));
    };
    const atFit = mapping();
    await gesture(() => c.d.flip(c.w, 'h'));
    triggerResize({ width: 1300, height: 820 });
    await settle();
    await undo();
    mapping().forEach((v, i) => expect(Math.abs(v - atFit[i]!)).toBeLessThanOrEqual(1e-4 * Math.max(1, Math.abs(v))));
  }));

  it('a new sample starts a new history', () => withSession(async c => {
    await gesture(() => c.d.flip(c.w, 'h'));
    expect(useUiStore().canUndo).toBe(true);
    c.store.targetData = JSON.parse(JSON.stringify(c.store.targetData));
    await settle();
    expect(useUiStore().canUndo).toBe(false);
  }));

  it('Cmd/Ctrl+Z undoes, Shift redoes, and fields keep their own undo', () => withSession(async c => {
    const fit = matHex(c.store.targetTransform);
    await gesture(() => c.d.flip(c.w, 'h'));
    const flipped = matHex(c.store.targetTransform);

    const input = document.querySelector('input[aria-label="Spot width"]') ?? document.createElement('input');
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true }));
    await settle();
    expect(matHex(c.store.targetTransform)).toBe(flipped);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', metaKey: true }));
    await settle();
    expect(matHex(c.store.targetTransform)).toBe(fit);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', metaKey: true, shiftKey: true }));
    await settle();
    expect(matHex(c.store.targetTransform)).toBe(flipped);
  }));
});

describe('floating islands', () => {
  const press = (el: EventTarget) => el.dispatchEvent(new Event('pointerdown', { bubbles: true }));

  it('the transform island opens one editor and closes on an outside press or Escape', () => withSession(async c => {
    const editor = () => document.querySelector('[role=group][aria-label=Rotation]');
    const chips = () => document.querySelectorAll('button[data-field]').length;
    expect(chips()).toBe(3);

    click(document.querySelector('button[data-field=rotation]'));
    await settle();
    expect(editor()).not.toBeNull();
    expect(chips()).toBe(0);

    press(editor()!.querySelector('input')!);
    await settle();
    expect(editor()).not.toBeNull();

    press(c.canvas());
    await settle();
    expect(editor()).toBeNull();
    expect(chips()).toBe(3);

    click(document.querySelector('button[data-field=rotation]'));
    await settle();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await settle();
    expect(editor()).toBeNull();
  }));

  it('the collapsible chrome cluster stays open on inside presses and closes outside', async () => {
    const w = mount(ChromeCluster, { props: { collapsible: true }, attachTo: document.body });
    const dots = () => w.find('button[aria-label="Links and appearance"]');
    expect(dots().exists()).toBe(true);

    await dots().trigger('click');
    expect(dots().exists()).toBe(false);
    const theme = w.find('[role=radio][aria-label="Dark appearance"]');
    press(theme.element);
    await theme.trigger('click');
    expect(w.find('[aria-label="GitHub repository"]').exists()).toBe(true);

    press(document.body);
    await w.vm.$nextTick();
    expect(dots().exists()).toBe(true);

    await dots().trigger('click');
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await w.vm.$nextTick();
    expect(dots().exists()).toBe(true);
    w.unmount();
  });

  it('the main GUI cluster is always open', () => {
    const w = mount(ChromeCluster, { attachTo: document.body });
    expect(w.find('button[aria-label="Links and appearance"]').exists()).toBe(false);
    expect(w.find('[aria-label="GitHub repository"]').exists()).toBe(true);
    w.unmount();
  });
});
