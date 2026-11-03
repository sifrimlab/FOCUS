/**
 * Multichannel image layers: toggling channels asks the backend for a re-rendered composite,
 * stores it apart from the layer data (so the layer is never re-fitted), and drops responses
 * superseded by a later toggle.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

const calls: { type: string; channels?: number[] }[] = [];
const pending: ((blob: Blob) => void)[] = [];

vi.mock('../src/api/client', () => ({
  api: {
    getPayload: (type: string, _responseType: string, channels?: number[]) => {
      calls.push({ type, channels });
      return new Promise<Blob>(resolve => pending.push(resolve));
    },
  },
  pollStatus: async () => null,
}));

import { useMainStore } from '../src/store/main';

describe('channel toggles', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    calls.length = 0;
    pending.length = 0;
  });

  it('requests the composite of the shown channels and keeps the layer data', async () => {
    const store = useMainStore();
    const data = new Blob(['initial']);
    store.targetData = data;
    const done = store.setChannels('target', [0, 2]);
    expect(store.targetChannels).toEqual([0, 2]);
    expect(calls).toEqual([{ type: 'target', channels: [0, 2] }]);
    const image = new Blob(['composite']);
    pending[0]!(image);
    await done;
    expect(store.targetImage).toBe(image);
    expect(store.targetData).toBe(data);
  });

  it('drops a response superseded by a later toggle', async () => {
    const store = useMainStore();
    const first = store.setChannels('reference', [0]);
    const second = store.setChannels('reference', [0, 1]);
    const late = new Blob(['late']);
    const latest = new Blob(['latest']);
    pending[1]!(latest);
    await second;
    pending[0]!(late);
    await first;
    expect(store.referenceImage).toBe(latest);
    expect(store.referenceChannels).toEqual([0, 1]);
  });
});
