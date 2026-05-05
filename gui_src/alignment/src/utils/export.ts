import { mat3, vec2 } from 'gl-matrix';
import type { Metadata, SpotModalityPayload } from '../api/types';

export function computeExportPayload(
  refMeta: Metadata,
  tgtMeta: Metadata,
  refData: any,
  tgtData: any,
  targetTransform: mat3,
  referenceTransform: mat3
): any {
  // Calculate mapping matrix T = M_ref^-1 * M_target
  // P_ref = T * P_target
  
  const invRef = mat3.create();
  mat3.invert(invRef, referenceTransform);
  
  const mapMatrix = mat3.create();
  mat3.multiply(mapMatrix, invRef, targetTransform);
  
  // Helper to transform a point
  const transformPoint = (x: number, y: number): [number, number] => {
    const v = vec2.fromValues(x, y);
    vec2.transformMat3(v, v, mapMatrix);
    return [v[0], v[1]];
  };

  // Helper to get scale factor
  const getScaleFactor = (): number => {
    const v0 = vec2.fromValues(0, 0);
    const v1 = vec2.fromValues(1, 0);
    vec2.transformMat3(v0, v0, mapMatrix);
    vec2.transformMat3(v1, v1, mapMatrix);
    return vec2.distance(v0, v1);
  };

  if (refMeta.modality_type === 'IMAGE' && tgtMeta.modality_type === 'IMAGE') {
    const [h, w] = tgtMeta.image_shape!;
    const corners = [
      [0, 0],
      [w, 0],
      [w, h],
      [0, h]
    ];
    
    const mappedCorners = corners.map(p => transformPoint(p[0]!, p[1]!));
    
    return {
      corner_pixels: mappedCorners,
      scale_factor: getScaleFactor()
    };
  }

  if (refMeta.modality_type === 'IMAGE' && tgtMeta.modality_type === 'SPOT') {
    const spots = tgtData as SpotModalityPayload;
    const spotSize = tgtMeta.spot_size!;
    const scale = getScaleFactor();
    const spotSizePx = [spotSize[0] * scale, spotSize[1] * scale];
    
    const mappedSpots = spots.map(spot => {
      const [px, py] = transformPoint(spot.spatial[0], spot.spatial[1]);
      
      return {
        id: (spot as any).id || spots.indexOf(spot), // Assuming ID exists or use index
        pixel_x: px,
        pixel_y: py
      };
    });

    return {
      spots: mappedSpots,
      spot_size_px: spotSizePx
    };
  }

  if (refMeta.modality_type === 'SPOT' && tgtMeta.modality_type === 'IMAGE') {
    const [h, w] = tgtMeta.image_shape!;
    const refSpots = refData as SpotModalityPayload;
    const refSpotSize = refMeta.spot_size!;
    const [rx, ry] = refSpotSize;
    const rx2 = rx / 2;
    const ry2 = ry / 2;

    // Down-sample grid
    const step = 10; 
    const mappedPixels = [];

    for (let y = 0; y < h; y += step) {
      for (let x = 0; x < w; x += step) {
        const [refX, refY] = transformPoint(x, y);
        
        // Find covering spot
        // Brute force for now
        let coveringSpotId = null;
        for (const spot of refSpots) {
          const [sx, sy] = spot.spatial;
          if (refX >= sx - rx2 && refX <= sx + rx2 &&
              refY >= sy - ry2 && refY <= sy + ry2) {
            coveringSpotId = (spot as any).id || refSpots.indexOf(spot);
            break;
          }
        }

        mappedPixels.push({
          x,
          y,
          covering_spot_id: coveringSpotId
        });
      }
    }

    return {
      pixels: mappedPixels
    };
  }

  if (refMeta.modality_type === 'SPOT' && tgtMeta.modality_type === 'SPOT') {
    const spots = tgtData as SpotModalityPayload;
    
    const mappedSpots = spots.map(spot => {
      const [refX, refY] = transformPoint(spot.spatial[0], spot.spatial[1]);
      
      return {
        id: (spot as any).id || spots.indexOf(spot),
        pixel_x: refX,
        pixel_y: refY
      };
    });

    return {
      spots: mappedSpots
    };
  }

  return {};
}
