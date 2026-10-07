/**
 * Golden equivalence test. Every modality pair and canvas size runs the same
 * scripted session; the recorded transforms (Float32 bytes), store writes and
 * the confirmed payload must match the files recorded from the original GUI.
 */
import { describe, it, expect } from 'vitest';
import { PAIRS } from './harness/fixtures';
import { runSession } from './harness/session';
import { SCRIPT, SIZES } from './script';
import { makeDriver } from './currentDriver';

describe('alignment numerics are unchanged', () => {
  for (const pair of PAIRS) {
    for (const size of SIZES) {
      it(`${pair.name} @ ${size.width}x${size.height}`, async () => {
        const out = await runSession(await makeDriver(), pair, size, SCRIPT);
        await expect(out).toMatchFileSnapshot(`./__golden__/${pair.name}-${size.width}x${size.height}.jsonl`);
      }, 30000);
    }
  }
});
