/**
 * Invariant golden test. Recorded before the alignment fixes (initial fit,
 * reset to fit, resize, rotate-drag mapping) with the old identity start, it
 * covers every operation those fixes did not touch. It must never change:
 * a mismatch means an untouched operation now computes different numbers.
 */
import { describe, it, expect } from 'vitest';
import { PAIRS } from './harness/fixtures';
import { runAppSession, runSession } from './harness/session';
import { APP_SCRIPT, INVARIANT_SCRIPT, SIZES } from './script';
import { makeDriver } from './currentDriver';

describe('operations untouched by the fixes are byte-identical', () => {
  for (const pair of PAIRS) {
    for (const size of SIZES) {
      it(`${pair.name} @ ${size.width}x${size.height}`, async () => {
        const out = await runSession(await makeDriver(), pair, size, INVARIANT_SCRIPT, { startAtIdentity: true });
        await expect(out).toMatchFileSnapshot(`./__golden__/invariant/${pair.name}-${size.width}x${size.height}.jsonl`);
      }, 30000);
    }
  }

  it('three samples through the app shell', async () => {
    const pairs = [PAIRS[0]!, PAIRS[3]!, PAIRS[1]!];
    const out = await runAppSession(await makeDriver(), pairs, { width: 1000, height: 700 }, APP_SCRIPT, { startAtIdentity: true });
    await expect(out).toMatchFileSnapshot('./__golden__/invariant/app-sequence.jsonl');
  }, 30000);
});
