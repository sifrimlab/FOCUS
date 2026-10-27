/** Native quads in bounded GPU meshes: no Graphics path per observation and no spatial pooling. */
import { Container, Mesh, MeshGeometry, Texture } from 'pixi.js';
import type { mat3 } from 'gl-matrix';
import type { Metadata, SpotModalityPayload } from '../api/types';
import type { SpotStyle } from './spotLayer';
import { projectiveTransformPoint } from '../utils/matrix';
import { classColor } from '../utils/colors';

export function spotCorners(x: number, y: number, style: SpotStyle): number[] {
  const basis = style.basis ?? [[1, 0], [0, 1]];
  const ux = basis[0]![0]! * style.spotSize[0] * style.boost / 2;
  const uy = basis[0]![1]! * style.spotSize[0] * style.boost / 2;
  const vx = basis[1]![0]! * style.spotSize[1] * style.boost / 2;
  const vy = basis[1]![1]! * style.spotSize[1] * style.boost / 2;
  return [x-ux-vx,y-uy-vy, x+ux-vx,y+uy-vy, x+ux+vx,y+uy+vy, x-ux+vx,y-uy+vy];
}

interface Chunk { mesh: Mesh<MeshGeometry>; rows: number[]; cls: number; foreground: boolean; shown: boolean }

export class NativeSpotLayer extends Container {
  private chunks: Chunk[] = [];
  private shapeKey = '';
  private data: SpotModalityPayload;

  constructor(data: SpotModalityPayload, meta: Metadata, target: boolean) {
    super();
    this.data = data;
    const groups = new Map<string, { rows: number[]; cls: number; foreground: boolean }>();
    for (let i = 0; i < data.length; i++) {
      const spot = data[i]!;
      const key = `${spot.class}:${Number(spot.foreground)}`;
      let group = groups.get(key);
      if (!group) { group = { rows: [], cls: spot.class, foreground: spot.foreground }; groups.set(key, group); }
      group.rows.push(i);
    }
    for (const group of groups.values()) {
      for (let start = 0; start < group.rows.length; start += 4096) {
        const rows = group.rows.slice(start, start + 4096);
        const indices = new Uint32Array(rows.length * 6);
        for (let j = 0; j < rows.length; j++) indices.set([4*j,4*j+1,4*j+2,4*j,4*j+2,4*j+3], j*6);
        const geometry = new MeshGeometry({ positions: new Float32Array(rows.length * 8), indices });
        geometry.batchMode = 'no-batch';
        const mesh = new Mesh({ geometry, texture: Texture.WHITE });
        mesh.tint = classColor(meta, group.cls, target);
        this.addChild(mesh);
        this.chunks.push({ mesh, rows, cls: group.cls, foreground: group.foreground, shown: true });
      }
    }
  }

  update(style: SpotStyle, matrix?: mat3) {
    const key = JSON.stringify([style.spotSize, style.boost, style.basis, matrix && Array.from(matrix)]);
    const rebuild = key !== this.shapeKey;
    const visible = new Set(style.classFilter);
    for (const chunk of this.chunks) {
      chunk.shown = visible.has(chunk.cls) && (style.foregroundMode === 'all'
        || chunk.foreground === (style.foregroundMode === 'foreground'));
      chunk.mesh.renderable = chunk.shown;
      if (!rebuild) continue;
      const positions = chunk.mesh.geometry.positions;
      for (let j = 0; j < chunk.rows.length; j++) {
        const spot = this.data[chunk.rows[j]!]!;
        const corners = spotCorners(spot.spatial[0], spot.spatial[1], style);
        if (matrix) {
          for (let k = 0; k < 8; k += 2) {
            const p = projectiveTransformPoint(matrix, corners[k]!, corners[k+1]!);
            corners[k] = p[0]; corners[k+1] = p[1];
          }
        }
        positions.set(corners, j * 8);
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
