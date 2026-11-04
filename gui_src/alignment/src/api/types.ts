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
}

export interface Spot {
  id?: number;
  spatial: [number, number];
  class: number;
  foreground: boolean;
  color?: string;
}

export type SpotModalityPayload = Spot[];

export interface ConfirmPayload {
  // Dynamic based on modality pair, defined in export logic
  [key: string]: any;
}
