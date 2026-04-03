export interface ParamSpec {
  type: 'bool' | 'int' | 'float' | 'string' | 'enum';
  default: unknown;
  nullable?: boolean;
  options?: string[];
}

export interface Schema {
  modality_types: string[];
  registration_types: string[];
  registration_compatibility: Record<string, string[] | null>;
  intensity_normalization: string[];
  background_color: string[];
  processing_params: Record<string, Record<string, ParamSpec>>;
  registration_params: Record<string, Record<string, ParamSpec>>;
}

export interface Modality {
  name: string;
  type: string;
  processing_settings: Record<string, unknown>;
  registration_type: string;
  registration_settings: Record<string, unknown>;
}

export interface Config {
  dataset_path: string;
  reference_modality: string;
  perform_alignment: boolean;
  perform_registration: boolean;
  huggingface_token: string | null;
  modalities: Modality[];
}

export interface PipelineStatus {
  state: 'idle' | 'running' | 'alignment_waiting' | 'completed' | 'error';
  stage: string | null;
  stage_index: number;
  total_stages: number;
  current_modality: string | null;
  current_sample: string | null;
  current_sample_index: number;
  total_samples: number;
  message: string;
  error: string | null;
  output_files: string[];
  alignment_port: number;
  sub_step: string | null;
  sub_step_index: number;
  sub_step_total: number;
  sub_step_progress: number;
  sub_step_items_total: number;
}

export interface ValidationResult {
  valid: boolean;
  config?: Config;
  errors?: string[];
  corrupted?: boolean;
}

export interface SamplesResult {
  samples: string[];
  has_existing_config: boolean;
}
