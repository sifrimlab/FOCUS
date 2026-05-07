import axios from 'axios';
import type { Schema, Config, PipelineStatus, ValidationResult, SamplesResult, BrowseResult, BrowseFilesResult } from './types';

const apiClient = axios.create({
  baseURL: '',
  headers: { 'Content-Type': 'application/json' },
});

export const api = {
  async getSchema(): Promise<Schema> {
    const r = await apiClient.get<Schema>('/api/schema');
    return r.data;
  },

  async getSamples(path: string): Promise<SamplesResult> {
    const r = await apiClient.get<SamplesResult>('/api/samples', { params: { path } });
    return r.data;
  },

  async browse(path: string): Promise<BrowseResult> {
    const r = await apiClient.get<BrowseResult>('/api/browse', { params: { path } });
    return r.data;
  },

  async browseFiles(path: string): Promise<BrowseFilesResult> {
    const r = await apiClient.get<BrowseFilesResult>('/api/browse_files', { params: { path } });
    return r.data;
  },

  async getConfig(): Promise<Config> {
    const r = await apiClient.get<Config>('/api/config');
    return r.data;
  },

  async putConfig(config: Config): Promise<Config> {
    const r = await apiClient.put<Config>('/api/config', config);
    return r.data;
  },

  async loadConfig(payload: { path?: string; content?: unknown }): Promise<ValidationResult> {
    const r = await apiClient.post<ValidationResult>('/api/config/load', payload);
    return r.data;
  },

  async loadExistingConfig(datasetPath: string): Promise<ValidationResult> {
    const r = await apiClient.post<ValidationResult>('/api/config/load-existing', { dataset_path: datasetPath });
    return r.data;
  },

  async validate(): Promise<ValidationResult> {
    const r = await apiClient.post<ValidationResult>('/api/validate');
    return r.data;
  },

  async run(): Promise<{ started: boolean }> {
    const r = await apiClient.post<{ started: boolean }>('/api/run');
    return r.data;
  },

  async getStatus(): Promise<PipelineStatus> {
    const r = await apiClient.get<PipelineStatus>('/api/status');
    return r.data;
  },

  async getState(): Promise<{
    config: Config;
    status: PipelineStatus;
    samples: string[];
    has_existing_config: boolean;
  }> {
    const r = await apiClient.get('/api/state');
    return r.data;
  },

  async cleanup(): Promise<{ deleted: number }> {
    const r = await apiClient.post<{ deleted: number }>('/api/cleanup');
    return r.data;
  },

  async reset(): Promise<void> {
    await apiClient.post('/api/reset');
  },

  async createSample(sample_id: string): Promise<{ sample_id: string }> {
    const r = await apiClient.post<{ sample_id: string }>('/api/samples/create', { sample_id });
    return r.data;
  },

  async ensureModalityFolders(modality_name: string, modality_type: string): Promise<{ updated_samples: string[] }> {
    const r = await apiClient.post<{ updated_samples: string[] }>('/api/modalities/create-folders', { modality_name, modality_type });
    return r.data;
  },
};
