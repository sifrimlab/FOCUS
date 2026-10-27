import { describe, expect, it } from 'vitest';
import { decodeSpots } from '../src/api/packedSpots';
import { classColor, getTargetColor, UNASSIGNED_COLOR } from '../src/utils/colors';
import { spotCorners, NativeSpotLayer } from '../src/canvas/nativeSpotLayer';
import { computeExportPayload } from '../src/utils/export';
import { targetFitMatrix } from '../src/canvas/layerFit';
import { mat3 } from 'gl-matrix';
import type { Metadata } from '../src/api/types';

function packed(n = 3) {
  const buffer = new ArrayBuffer(16 + 17 * n);
  const h = new DataView(buffer);
  h.setUint32(0, 0x31505346, true); h.setUint32(4, n, true); h.setUint32(8, 1, true);
  const xy = new Float32Array(buffer, 16, 2 * n);
  const classes = new Uint32Array(buffer, 16 + 8 * n, n);
  const ids = new Uint32Array(buffer, 16 + 12 * n, n);
  const fg = new Uint8Array(buffer, 16 + 16 * n, n);
  for (let i = 0; i < n; i++) { xy.set([i * 16, (i % 5) * 16], 2 * i); classes[i] = i % 33; ids[i] = n - i; fg[i] = i % 2; }
  return buffer;
}

describe('native spot transport and display contract', () => {
  it('decodes unaligned observation counts with array-compatible iteration and original IDs', () => {
    const data = decodeSpots(packed());
    expect(Array.isArray(data)).toBe(true);
    expect(data.length).toBe(3);
    expect(data[2]).toEqual({ id: 1, spatial: [32, 32], class: 2, foreground: false });
    expect(data.map(s => s.id)).toEqual([3, 2, 1]);
    expect([...data].map(s => s.spatial)).toEqual([[0, 0], [16, 16], [32, 32]]);
  });

  it('validates the wire format before constructing typed views', () => {
    expect(() => decodeSpots(new ArrayBuffer(0))).toThrow('Truncated');
    const invalid = packed(); new DataView(invalid).setUint32(4, 99, true);
    expect(() => decodeSpots(invalid)).toThrow('length');
  });

  it('keeps the fit and exported mapping identical to an ordinary array above the old cap', () => {
    const data = decodeSpots(packed(100_003));
    const plain = Array.from(data);
    const meta: Metadata = { modality_type: 'SPOT', modality_name: 'ST', spot_size: [16, 16] };
    expect(Array.from(targetFitMatrix(1000, 700, 1, meta, data)))
      .toEqual(Array.from(targetFitMatrix(1000, 700, 1, meta, plain)));
    const image: Metadata = { modality_type: 'IMAGE', modality_name: 'image', image_shape: [500, 500] };
    const transform = mat3.fromValues(1, .2, 0, .1, 1, 0, 30, 20, 1);
    const full = computeExportPayload(image, meta, null, data, transform, mat3.create());
    const compact = computeExportPayload(image, meta, null, [], transform, mat3.create());
    expect(compact.transform_matrix).toEqual(full.transform_matrix);
    expect(full.spots.length).toBe(100_003);
  });

  it('keeps saved labels, per-layer color families without cycling, and a neutral unassigned', () => {
    const labels = Array.from({ length: 100 }, (_, i) => String(i)).concat('unassigned');
    const meta: Metadata = { modality_type: 'SPOT', modality_name: 'ST', cluster_labels: labels };
    expect(meta.cluster_labels![10]).toBe('10');
    const target = labels.map((_, i) => classColor(meta, i, true));
    const reference = labels.map((_, i) => classColor(meta, i, false));
    for (const colors of [target, reference]) {
      expect(new Set(colors).size).toBe(labels.length);
      for (const color of colors) expect(color).toMatch(/^#[0-9a-f]{6}$/);
    }
    expect(target[100]).toBe(UNASSIGNED_COLOR);
    expect(reference[100]).toBe(UNASSIGNED_COLOR);
    // Unrelated partitions in the two layers never share a color.
    expect(target.slice(0, 100).filter(c => reference.includes(c))).toEqual([]);
    expect(target.slice(0, 16)).toEqual(Array.from({ length: 16 }, (_, i) => getTargetColor(i)));
  });

  it('draws acquisition-oriented squares with their original dimensions', () => {
    const a = .3; const u: [number, number] = [Math.cos(a), Math.sin(a)];
    const v: [number, number] = [-u[1], u[0]];
    const q = spotCorners(10, 20, { spotSize: [16, 16], boost: 1, basis: [u, v], classFilter: [0], foregroundMode: 'all' });
    expect(Math.hypot(q[2]! - q[0]!, q[3]! - q[1]!)).toBeCloseTo(16);
    expect((q[0]! + q[4]!) / 2).toBeCloseTo(10);
    expect((q[1]! + q[5]!) / 2).toBeCloseTo(20);
  });

  it('renders all native observations in bounded meshes and releases their buffers', () => {
    const data = decodeSpots(packed(10_003));
    const layer = new NativeSpotLayer(data, { modality_type: 'SPOT', modality_name: 'ST' }, true);
    const style = { spotSize: [16, 16] as [number, number], boost: 1, classFilter: Array.from({ length: 33 }, (_, i) => i), foregroundMode: 'all' as const };
    layer.update(style);
    const indices = layer.children.reduce((n, c: any) => n + c.geometry.indices.length, 0);
    expect(indices).toBe(6 * data.length);
    for (const mesh of layer.children as any[]) expect(mesh.geometry.positions.length).toBeLessThanOrEqual(4096 * 8);
    layer.update({ ...style, classFilter: [] });
    expect(layer.children.every((c: any) => !c.renderable)).toBe(true);
    layer.destroy();
  });
});
