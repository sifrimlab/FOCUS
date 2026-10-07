/**
 * App-level golden test: loading, confirming and moving to the next sample
 * through the real shell. Guards the mount/unmount timing of the canvases
 * around sample changes, which decides each sample's starting transforms.
 */
import { describe, it, expect } from 'vitest';
import { PAIRS } from './harness/fixtures';
import { runAppSession } from './harness/session';
import { APP_SCRIPT } from './script';
import { makeDriver } from './currentDriver';

describe('sample sequence through the app shell is unchanged', () => {
  it('three samples, confirm each', async () => {
    const pairs = [PAIRS[0]!, PAIRS[3]!, PAIRS[1]!];
    const out = await runAppSession(await makeDriver(), pairs, { width: 1000, height: 700 }, APP_SCRIPT);
    await expect(out).toMatchFileSnapshot('./__golden__/app-sequence.jsonl');
  }, 30000);
});
