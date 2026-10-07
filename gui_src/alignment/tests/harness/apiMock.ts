/** In-memory backend: serves one sample per queued pair and records confirms. */
import type { Side } from './fixtures';
import { STATUS } from './fixtures';

interface Pending { ref: Side; tgt: Side; refData: unknown; tgtData: unknown }

export const backend = {
  queue: [] as Pending[],
  current: null as Pending | null,
  confirmed: [] as unknown[],
  enqueue(ref: Side, tgt: Side) {
    this.queue.push({ ref, tgt, refData: ref.data(), tgtData: tgt.data() });
  },
  reset() { this.queue = []; this.current = null; this.confirmed = []; },
};

export async function pollStatus() {
  backend.current = backend.queue.shift() ?? null;
  return backend.current ? { ...STATUS } : null;
}

export const api = {
  async getStatus() { return pollStatus(); },
  async getMetadata(type: 'reference' | 'target') {
    const side = type === 'reference' ? backend.current!.ref : backend.current!.tgt;
    return structuredClone(side.meta);
  },
  async getPayload(type: 'reference' | 'target') {
    return type === 'reference' ? backend.current!.refData : backend.current!.tgtData;
  },
  async confirm(payload: unknown) { backend.confirmed.push(payload); },
};
