/** Native footprints in bounded GPU meshes: no Graphics path per observation and no spatial pooling. */
import { Container, Mesh, MeshGeometry, Texture } from 'pixi.js';
import type { Metadata, SpotModalityPayload, SpotShape } from '../api/types';
import type { SpotStyle } from './spotLayer';
import type { PointMap } from './warpView';
import type { SpotDisplay } from '../utils/spotDisplay';
import { classColor } from '../utils/colors';

const H = 1 / Math.sqrt(3);
/** Unit footprints in (u, v) lattice units, counter-clockwise. The hexagon is the Voronoi cell
 * of a hexagonal lattice with neighbours along u: 1 across the flats along u, 2/sqrt(3) along v. */
const TEMPLATES: Record<SpotShape, number[]> = {
  rectangle: [-.5,-.5, .5,-.5, .5,.5, -.5,.5],
  hexagon: [.5,-H/2, .5,H/2, 0,H, -.5,H/2, -.5,-H/2, 0,-H],
};

/** Corner offsets from the spot centre: the template scaled by spot size and boost, on the basis. */
function footprintOffsets(style: SpotStyle, shape: SpotShape): number[] {
  const [[ux, uy], [vx, vy]] = style.basis ?? [[1, 0], [0, 1]];
  const su = style.spotSize[0] * style.boost;
  const sv = style.spotSize[1] * style.boost;
  const t = TEMPLATES[shape];
  const out = new Array<number>(t.length);
  for (let k = 0; k < t.length; k += 2) {
    out[k] = t[k]! * su * ux + t[k+1]! * sv * vx;
    out[k+1] = t[k]! * su * uy + t[k+1]! * sv * vy;
  }
  return out;
}

export function spotCorners(x: number, y: number, style: SpotStyle, shape: SpotShape = 'rectangle'): number[] {
  return footprintOffsets(style, shape).map((d, k) => d + (k % 2 ? y : x));
}

interface Chunk { mesh: Mesh<MeshGeometry>; rows: number[]; cls: number; foreground: boolean; shown: boolean }

export class NativeSpotLayer extends Container {
  private chunks: Chunk[] = [];
  private shapeKey = '';
  private data: SpotModalityPayload;
  private shape: SpotShape;

  /** Meshes group spots by class, or by `display` key (clusters at a resolution, feature levels). */
  constructor(data: SpotModalityPayload, meta: Metadata, target: boolean, display: SpotDisplay | null = null) {
    super();
    this.data = data;
    this.shape = meta.spot_shape ?? 'rectangle';
    const corners = TEMPLATES[this.shape].length / 2;
    // Triangle fan over each convex footprint.
    const fan = Array.from({ length: corners - 2 }, (_, t) => [0, t + 1, t + 2]).flat();
    const groups = new Map<string, { rows: number[]; cls: number; foreground: boolean }>();
    for (let i = 0; i < data.length; i++) {
      const spot = data[i]!;
      const cls = display ? display.keys[i]! : spot.class;
      const key = `${cls}:${Number(spot.foreground)}`;
      let group = groups.get(key);
      if (!group) { group = { rows: [], cls, foreground: spot.foreground }; groups.set(key, group); }
      group.rows.push(i);
    }
    for (const group of groups.values()) {
      for (let start = 0; start < group.rows.length; start += 4096) {
        const rows = group.rows.slice(start, start + 4096);
        const indices = new Uint32Array(rows.length * fan.length);
        for (let j = 0; j < rows.length; j++) indices.set(fan.map(i => corners * j + i), j * fan.length);
        const geometry = new MeshGeometry({ positions: new Float32Array(rows.length * corners * 2), indices });
        geometry.batchMode = 'no-batch';
        const mesh = new Mesh({ geometry, texture: Texture.WHITE });
        mesh.tint = display ? display.colors[group.cls]! : classColor(meta, group.cls, target);
        this.addChild(mesh);
        this.chunks.push({ mesh, rows, cls: group.cls, foreground: group.foreground, shown: true });
      }
    }
  }

  /** Rebuilds positions when the style or the point map changed; `map` absent: native positions. */
  update(style: SpotStyle, map?: PointMap) {
    const key = JSON.stringify([style.spotSize, style.boost, style.basis, map?.key]);
    const rebuild = key !== this.shapeKey;
    const visible = new Set(style.classFilter);
    const offsets = footprintOffsets(style, this.shape);
    for (const chunk of this.chunks) {
      chunk.shown = visible.has(chunk.cls) && (style.foregroundMode === 'all'
        || chunk.foreground === (style.foregroundMode === 'foreground'));
      chunk.mesh.renderable = chunk.shown;
      if (!rebuild) continue;
      const positions = chunk.mesh.geometry.positions;
      for (let j = 0; j < chunk.rows.length; j++) {
        const [x, y] = this.data[chunk.rows[j]!]!.spatial;
        const corner = map?.forSpot(x, y);
        for (let k = 0; k < offsets.length; k += 2) {
          let px = x + offsets[k]!, py = y + offsets[k+1]!;
          if (corner) [px, py] = corner(px, py);
          positions[j * offsets.length + k] = px; positions[j * offsets.length + k + 1] = py;
        }
      }
      chunk.mesh.geometry.getBuffer('aPosition').update();
    }
    this.shapeKey = key;
  }

  cull(screen: { width: number; height: number }) {
    for (const chunk of this.chunks) {
      if (!chunk.shown) { chunk.mesh.renderable = false; continue; }
      const b = chunk.mesh.getBounds();
      chunk.mesh.renderable = b.maxX >= 0 && b.maxY >= 0 && b.minX <= screen.width && b.minY <= screen.height;
    }
  }

  override destroy() {
    for (const { mesh } of this.chunks) { const geometry = mesh.geometry; mesh.destroy(); geometry.destroy(); }
    this.chunks = [];
    super.destroy({ children: true });
  }
}
