/**
 * Minimal pixi.js stand-in. The canvases only use Pixi to draw; every number
 * that reaches the store comes from app.screen and from Texture sizes, which
 * this mock reproduces deterministically.
 */
export const screenSize = { width: 1000, height: 700 };
export const apps: Application[] = [];
/**
 * One entry per render: which app rendered and how much layer content
 * (filled shapes plus sprites in its content container) it held at that time.
 */
export const renders: Array<{ app: Application; drawn: number }> = [];

class Point {
  x = 0; y = 0;
  set(x: number, y?: number) { this.x = x; this.y = y ?? x; }
}

export class Container {
  children: any[] = [];
  position = new Point();
  scale = new Point();
  pivot = new Point();
  skew = new Point();
  rotation = 0;
  alpha = 1;
  x = 0; y = 0;
  addChild(c: any) { this.children.push(c); return c; }
  removeChildren() { const c = this.children; this.children = []; return c; }
  destroy() {}
}

const chain = function (this: any) { return this; };
export class Graphics extends Container {
  fills = 0;
  rect = chain; poly = chain; circle = chain;
  clear() { this.fills = 0; return this; }
  fill() { this.fills += 1; return this; }
  moveTo = chain; lineTo = chain; stroke = chain; setStrokeStyle = chain;
}

export class Sprite extends Container {
  constructor(public texture: any) { super(); }
}

export const Texture = {
  from(img: { width: number; height: number }) {
    return { width: img.width, height: img.height };
  },
};

export class Application {
  screen = { width: 0, height: 0 };
  canvas!: HTMLCanvasElement;
  stage = new Container();
  renderer: object | null = {};
  async init(_opts: Record<string, unknown>) {
    apps.push(this);
    this.canvas = document.createElement('canvas');
    this.canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, x: 0, y: 0, right: this.screen.width, bottom: this.screen.height,
        width: this.screen.width, height: this.screen.height, toJSON() {} }) as DOMRect;
    this.resize();
  }
  resize() { this.screen = { width: screenSize.width, height: screenSize.height }; }
  render() {
    const content = this.stage.children[0]?.children[0] as Container | undefined;
    const drawn = (content?.children ?? []).reduce((n, c) => n + (c instanceof Graphics ? c.fills : 1), 0);
    renders.push({ app: this, drawn });
  }
  destroy() { this.renderer = null; apps.splice(apps.indexOf(this), 1); }
}
