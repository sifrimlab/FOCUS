import axios from 'axios';
import type { Schema, Config, PipelineStatus, ValidationResult, SamplesResult } from './types';

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

  async reset(): Promise<void> {
    await apiClient.post('/api/reset');
  },
};
