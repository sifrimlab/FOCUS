/**
 * jsdom stubs for the browser APIs the canvases use. They only make the code
 * run; they never feed numbers into the alignment math except the image size,
 * which comes from the fixture blob exactly as a decoded PNG would provide it.
 */
import { vi } from 'vitest';
import { blobSize } from './fixtures';

const urlToBlob = new Map<string, Blob>();
let urlCounter = 0;
URL.createObjectURL = (b: Blob) => {
  const url = `blob:fixture/${urlCounter++}`;
  urlToBlob.set(url, b);
  return url;
};
URL.revokeObjectURL = () => {};

class FakeImage {
  src = '';
  width = 0;
  height = 0;
  async decode() {
    const blob = urlToBlob.get(this.src);
    const size = blob ? blobSize(blob) : null;
    this.width = size?.width ?? 0;
    this.height = size?.height ?? 0;
  }
}
(globalThis as any).Image = FakeImage;

export const resizeCallbacks: Array<() => void> = [];
class FakeResizeObserver {
  constructor(private cb: () => void) {}
  observe() { resizeCallbacks.push(this.cb); }
  disconnect() {
    const i = resizeCallbacks.indexOf(this.cb);
    if (i >= 0) resizeCallbacks.splice(i, 1);
  }
}
(globalThis as any).ResizeObserver = FakeResizeObserver;

vi.mock('pixi.js', async () => await import('./pixiMock'));
vi.mock('../../src/api/client', async () => await import('./apiMock'));
