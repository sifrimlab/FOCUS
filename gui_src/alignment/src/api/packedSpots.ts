import type { Spot, SpotModalityPayload } from './types';

/** Lazy array compatibility keeps the guarded fit/export math unchanged, without allocating
 * an object and coordinate array for every spot. The backing storage stays in typed arrays. */
export function decodeSpots(buffer: ArrayBuffer): SpotModalityPayload {
  if (buffer.byteLength < 16) throw new Error('Truncated spot payload');
  const header = new DataView(buffer);
  const version = header.getUint32(8, true);
  if (header.getUint32(0, true) !== 0x31505346 || (version !== 1 && version !== 2)) {
    throw new Error('Unsupported spot payload');
  }
  const n = header.getUint32(4, true);
  // Version 2 appends nFeatures uint8 feature images per spot (header field 3).
  const nFeatures = version === 2 ? header.getUint32(12, true) : 0;
  if (buffer.byteLength !== 16 + n * (17 + nFeatures)) throw new Error('Invalid spot payload length');
  const xy = new Float32Array(buffer, 16, n * 2);
  const classes = new Uint32Array(buffer, 16 + n * 8, n);
  const ids = new Uint32Array(buffer, 16 + n * 12, n);
  const foreground = new Uint8Array(buffer, 16 + n * 16, n);
  const at = (i: number): Spot => ({
    id: ids[i]!, spatial: [xy[i * 2]!, xy[i * 2 + 1]!],
    class: classes[i]!, foreground: Boolean(foreground[i]),
  });
  const array = new Array<Spot>(n) as SpotModalityPayload;
  array.classes = classes;
  if (nFeatures > 0) {
    array.features = new Uint8Array(buffer, 16 + n * 17, n * nFeatures);
    array.nFeatures = nFeatures;
  }
  const index = (key: PropertyKey) => typeof key === 'string' && /^(0|[1-9]\d*)$/.test(key) ? Number(key) : -1;
  return new Proxy(array, {
    get(target, key, receiver) {
      const i = index(key);
      return i >= 0 && i < n ? at(i) : Reflect.get(target, key, receiver);
    },
    has(target, key) {
      const i = index(key);
      return i >= 0 && i < n || Reflect.has(target, key);
    },
  });
}
