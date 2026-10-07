/**
 * Deterministic sample fixtures for the four modality pairs.
 * IMAGE payloads are Blobs tagged with their pixel size (read back by the
 * FakeImage stub); SPOT payloads are generated on a jittered grid.
 */
import type { Metadata, SampleStatus, SpotModalityPayload } from '../../src/api/types';

const sizes = new WeakMap<Blob, { width: number; height: number }>();
export const blobSize = (b: Blob) => sizes.get(b) ?? null;

function imageBlob(width: number, height: number): Blob {
  const b = new Blob([new Uint8Array(8)], { type: 'image/png' });
  sizes.set(b, { width, height });
  return b;
}

function spotGrid(opts: { nx: number; ny: number; pitch: number; x0: number; y0: number; classes: number; withId: boolean }): SpotModalityPayload {
  const spots: any[] = [];
  let k = 0;
  for (let j = 0; j < opts.ny; j++) {
    for (let i = 0; i < opts.nx; i++) {
      const jitter = ((k * 37) % 11) / 10;
      const spot: any = {
        spatial: [opts.x0 + i * opts.pitch + jitter, opts.y0 + j * opts.pitch * 0.866 + (i % 2) * 0.5 * opts.pitch],
        class: (i + 2 * j) % opts.classes,
        foreground: (i * j) % 3 !== 0,
      };
      if (opts.withId) spot.id = 1000 + k;
      spots.push(spot);
      k++;
    }
  }
  return spots;
}

export interface Side { meta: Metadata; data: () => Blob | SpotModalityPayload }

export const SIDES = {
  refImage: { meta: { modality_type: 'IMAGE', modality_name: 'H&E', image_shape: [300, 400] }, data: () => imageBlob(400, 300) },
  tgtImage: { meta: { modality_type: 'IMAGE', modality_name: 'IF', image_shape: [210, 263], scaling_factor: 0.8 }, data: () => imageBlob(263, 210) },
  refSpot: {
    meta: { modality_type: 'SPOT', modality_name: 'Visium', spot_size: [55, 55] },
    data: () => spotGrid({ nx: 7, ny: 6, pitch: 100, x0: 1200, y0: 800, classes: 3, withId: true }),
  },
  tgtSpot: {
    meta: { modality_type: 'SPOT', modality_name: 'MSI', spot_size: [10, 12], scaling_factor: 1.25 },
    data: () => spotGrid({ nx: 9, ny: 8, pitch: 20, x0: 5, y0: 3, classes: 4, withId: false }),
  },
  tgtSpotMicrogrid: {
    meta: { modality_type: 'SPOT', modality_name: 'Microgrid', spot_size: [8, 8], foreground_only: true },
    data: () => spotGrid({ nx: 6, ny: 5, pitch: 15, x0: 40, y0: 60, classes: 2, withId: true }),
  },
} satisfies Record<string, Side>;

export const PAIRS: Array<{ name: string; ref: Side; tgt: Side }> = [
  { name: 'image-image', ref: SIDES.refImage, tgt: SIDES.tgtImage },
  { name: 'image-spot', ref: SIDES.refImage, tgt: SIDES.tgtSpot },
  { name: 'spot-image', ref: SIDES.refSpot, tgt: SIDES.tgtImage },
  { name: 'spot-spot', ref: SIDES.refSpot, tgt: SIDES.tgtSpot },
  { name: 'spot-microgrid', ref: SIDES.refSpot, tgt: SIDES.tgtSpotMicrogrid },
];

export const STATUS: SampleStatus = { sample_id: 'sample_03', sample_index: 3, total_samples_count: 12 };
