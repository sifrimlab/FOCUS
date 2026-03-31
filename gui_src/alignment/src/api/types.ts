export interface SampleStatus {
  sample_id: string;
  sample_index: number;
  total_samples_count: number;
}

export type ModalityType = 'IMAGE' | 'SPOT';

export interface Metadata {
  modality_type: ModalityType;
  modality_name: string;
  spot_size?: [number, number];
  image_shape?: [number, number];
  scaling_factor?: number;
  color_map?: Record<string, string>;
}

export interface Spot {
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
