export interface ParamSpec {
  type: 'bool' | 'int' | 'float' | 'string' | 'enum' | 'path';
  default: unknown;
  nullable?: boolean;
  options?: string[];
}

export interface Schema {
  modality_types: string[];
  registration_types: string[];
  registration_compatibility: Record<string, string[] | null>;
  alignment_strategies: string[];
  alignment_strategy_compatibility: Record<string, string[] | null>;
  intensity_normalization: string[];
  background_color: string[];
  annotation_file_types: string[];
  processing_params: Record<string, Record<string, ParamSpec>>;
  registration_params: Record<string, Record<string, ParamSpec>>;
  display_names: Record<string, string>;
}

export interface Modality {
  name: string;
  type: string;
  processing_settings: Record<string, unknown>;
  registration_type: string;
  registration_settings: Record<string, unknown>;
  alignment_strategy: string;
}

export interface SpatialAnnotations {
  modality_name: string;
  file_type: string;
}

export interface Config {
  dataset_path: string;
  reference_modality: string;
  perform_alignment: boolean;
  alignment_force_recomputing: boolean;
  perform_registration: boolean;
  huggingface_token: string | null;
  spatial_annotations: SpatialAnnotations | null;
  modalities: Modality[];
  ignore_samples: string[];
  samples: string[];
  last_edit: string | null;
}

export interface OutputSection {
  merged: string[];
  per_modality: Record<string, string[]>;  // modality_name → per-sample paths
}

export interface OutputFiles {
  preprocessing?: OutputSection;
  alignment?: OutputSection;
  annotations?: OutputSection;
  registration?: OutputSection;
  multimodal?: OutputSection;
}

export interface PipelineStatus {
  state: 'idle' | 'running' | 'alignment_waiting' | 'completed' | 'error';
  stage: string | null;
  stage_index: number;
  total_stages: number;
  current_modality: string | null;
  current_modality_index: number;
  total_modalities: number;
  current_sample: string | null;
  current_sample_index: number;
  total_samples: number;
  message: string;
  error: string | null;
  output_files: OutputFiles;
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

export interface BrowseResult {
  path: string;
  parent: string | null;
  entries: { name: string }[];
}

export interface BrowseFilesResult {
  path: string;
  parent: string | null;
  entries: { name: string; is_dir: boolean }[];
}
