export interface SampleStatus {
  sample_id: string;
  sample_index: number;
  total_samples_count: number;
}

export type ModalityType = 'IMAGE' | 'SPOT';

export type SpotShape = 'rectangle' | 'hexagon';

/** One channel of a multichannel image: display colour (RRGGBB) and default visibility. */
export interface ImageChannel {
  name: string;
  color: string;
  visible: boolean;
}

export interface Metadata {
  modality_type: ModalityType;
  modality_name: string;
  spot_size?: [number, number];
  image_shape?: [number, number];
  scaling_factor?: number;
  color_map?: Record<string, string>;
  cluster_labels?: string[];
  spot_encoding?: 'FSP1';
  spot_count?: number;
  spot_basis?: [[number, number], [number, number]];
  // Footprint drawn on spot_basis: hexagon on hexagonal lattices, rectangle otherwise
  spot_shape?: SpotShape;
  // Multichannel images: channels the payload can composite (?channels=0,2)
  channels?: ImageChannel[];
  // Exact-spot mode (microgrid experiments): only foreground spots are sent
  foreground_only?: boolean;
  // Clustered with a merge tree: cluster_labels are the fine clusters; a resolution of L clusters
  // applies the first (n_fine - L) merges (pairs of fine labels).
  cluster_tree?: { merges: [string, string][]; default_level: number };
  // Labels of the per-spot feature images (ions, genes) appended to an FSP v2 payload.
  feature_images?: string[];
}

export interface Spot {
  id?: number;
  spatial: [number, number];
  class: number;
  foreground: boolean;
  color?: string;
}

export type SpotModalityPayload = Spot[] & {
  /** FSP v2: per-spot feature images, spot-major (n x nFeatures), 0 = no value. */
  features?: Uint8Array;
  nFeatures?: number;
  /** FSP: class index of every spot, without building Spot objects. */
  classes?: Uint32Array;
};

export interface ConfirmPayload {
  // Dynamic based on modality pair, defined in export logic
  [key: string]: any;
}
