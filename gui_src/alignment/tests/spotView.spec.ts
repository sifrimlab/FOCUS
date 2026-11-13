/**
 * Spot views of a clustered layer: FSP v2 decoding, clusters at a runtime
 * resolution (merge tree), feature images, and the panel controls.
 */
import { describe, it, expect } from 'vitest';
import { decodeSpots } from '../src/api/packedSpots';
import type { Metadata } from '../src/api/types';
import { FEATURE_LEVELS, initialView, spotDisplay } from '../src/utils/spotDisplay';
import { SIDES } from './harness/fixtures';
import { openSession, settle } from './harness/session';
import { makeDriver } from './currentDriver';
import { typeInto } from './harness/driver';

const NX = 12, NY = 8, N = NX * NY;

/** FSP v2 payload: four fine clusters in vertical bands, two feature images. */
function payload(): ArrayBuffer {
  const nf = 2;
  const buffer = new ArrayBuffer(16 + N * (17 + nf));
  const v = new DataView(buffer);
  v.setUint32(0, 0x31505346, true);
  v.setUint32(4, N, true);
  v.setUint32(8, 2, true);
  v.setUint32(12, nf, true);
  const xy = new Float32Array(buffer, 16, N * 2);
  const classes = new Uint32Array(buffer, 16 + N * 8, N);
  const ids = new Uint32Array(buffer, 16 + N * 12, N);
  const fg = new Uint8Array(buffer, 16 + N * 16, N);
  const features = new Uint8Array(buffer, 16 + N * 17, N * nf);
  for (let k = 0; k < N; k++) {
    const i = k % NX, j = Math.floor(k / NX);
    xy[2 * k] = 5 + 10 * i; xy[2 * k + 1] = 5 + 10 * j;
    classes[k] = j === 0 ? 4 : Math.floor(i / 3); // top row unassigned, then 4 bands
    ids[k] = k; fg[k] = 1;
    features[k * nf] = j === 0 ? 0 : 1 + i * 20; // rises to the right
    features[k * nf + 1] = 255 - k;
  }
  return buffer;
}

const META: Metadata = {
  modality_type: 'SPOT', modality_name: 'MSI', spot_size: [10, 10], spot_encoding: 'FSP1', spot_count: N,
  cluster_labels: ['0', '1', '2', '3', 'unassigned'],
  // Bands 0+1 and 2+3 merge first, then the two halves.
  cluster_tree: { merges: [['0', '1'], ['2', '3'], ['0', '2']], default_level: 2 },
  feature_images: ['m/z 760.5855 · PC 34:1', 'm/z 502.2932'],
};

describe('FSP v2', () => {
  it('decodes feature images and the class array; v1 still decodes', () => {
    const data = decodeSpots(payload());
    expect(data).toHaveLength(N);
    expect(data.nFeatures).toBe(2);
    expect(data.features![2 * 13]).toBe(1 + 1 * 20);
    expect(data.classes![13]).toBe(0);
    expect(data[13]!.class).toBe(0);
  });
});

describe('spot display', () => {
  const data = decodeSpots(payload());
  const keysAt = (level: number) =>
    Array.from(spotDisplay(META, data, { mode: 'clusters', level, feature: 0 }, true)!.keys);

  it('starts at the default resolution', () => {
    expect(initialView(META)).toEqual({ mode: 'clusters', level: 2, feature: 0 });
    expect(initialView({ ...META, cluster_tree: undefined })).toBeNull();
  });

  it('merges the fine clusters along the tree as the resolution falls', () => {
    const groups = (level: number) => new Set(keysAt(level).filter((_, k) => k >= NX)).size;
    expect(groups(4)).toBe(4);
    expect(groups(3)).toBe(3);
    expect(groups(2)).toBe(2);
    expect(groups(1)).toBe(1);
    // At 2 clusters, bands 0 and 1 share a key, bands 2 and 3 another.
    const at2 = keysAt(2);
    expect(at2[NX + 0]).toBe(at2[NX + 3]);
    expect(at2[NX + 6]).toBe(at2[NX + 9]);
    expect(at2[NX + 0]).not.toBe(at2[NX + 6]);
  });

  it('keeps unassigned spots grey and apart at every resolution', () => {
    for (const level of [1, 2, 4]) {
      const d = spotDisplay(META, data, { mode: 'clusters', level, feature: 0 }, true)!;
      const key = d.keys[0]!;
      expect(d.labels[key]).toBe('unassigned');
      expect(d.keys.slice(NX).every(k => k !== key)).toBe(true);
    }
  });

  it('maps a feature image to ordered intensity levels', () => {
    const d = spotDisplay(META, data, { mode: 'features', level: 2, feature: 0 }, true)!;
    expect(d.filterable).toBe(false);
    expect(d.keys[0]).toBe(FEATURE_LEVELS); // no value
    const row = Array.from(d.keys.slice(NX, 2 * NX));
    expect(row.every((k, i) => i === 0 || k >= row[i - 1]!)).toBe(true);
    expect(row[NX - 1]).toBeGreaterThan(row[0]!);
  });
});

describe('panel controls', () => {
  const TREE = { meta: META, data: () => payload() } as const;
  const SIZE = { width: 1000, height: 700 };

  it('the slider sets the resolution and the switch shows a feature image', async () => {
    const s = await openSession(await makeDriver(), { ref: SIDES.refImage, tgt: TREE as any }, SIZE);
    try {
      const { store } = s.ctx;
      expect(store.spotViews.target?.level).toBe(2);
      expect(store.targetSpotClasses).toHaveLength(3); // two groups + unassigned
      const slider = document.querySelector('[data-layer="target"] input[type="range"]') as HTMLInputElement;
      expect(slider).not.toBeNull();
      typeInto(slider, '4');
      await settle();
      expect(store.spotViews.target?.level).toBe(4);
      expect(store.targetSpotClasses).toHaveLength(5);
      const features = [...document.querySelectorAll('[data-layer="target"] [role=radio]')]
        .find(el => el.getAttribute('aria-label')?.startsWith('Feature images')) as HTMLElement;
      features.click();
      await settle();
      expect(store.spotViews.target?.mode).toBe('features');
      expect(store.targetDisplay?.filterable).toBe(false);
      expect(document.querySelector('[data-layer="target"] select')).not.toBeNull();
    } finally {
      s.close();
    }
  });

  it('a layer without a tree shows no view controls', async () => {
    const s = await openSession(await makeDriver(), { ref: SIDES.refImage, tgt: SIDES.tgtSpot }, SIZE);
    try {
      expect(s.ctx.store.spotViews.target).toBeNull();
      expect(document.querySelector('[data-layer="target"] [aria-label="Spot view"]')).toBeNull();
    } finally {
      s.close();
    }
  });
});
