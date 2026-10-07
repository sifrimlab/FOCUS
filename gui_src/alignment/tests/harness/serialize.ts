/** Exact, byte-level renderings of matrices and payloads for golden comparison. */
export function matHex(m: ArrayLike<number> & { buffer?: ArrayBufferLike }): string {
  const f = m instanceof Float32Array ? m : Float32Array.from(m as ArrayLike<number>);
  return Buffer.from(f.buffer, f.byteOffset, f.byteLength).toString('hex');
}

export function exactJson(value: unknown): string {
  // JSON.stringify emits the shortest round-trip representation of each
  // float64, so equal strings imply bit-identical numbers.
  return JSON.stringify(value, (_k, v) => (v instanceof Float32Array ? Array.from(v) : v));
}
