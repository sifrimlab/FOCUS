import { defineStore } from 'pinia';
import { api } from '../api/client';
import type { Schema, Config, PipelineStatus, Modality } from '../api/types';

function emptyConfig(): Config {
  return {
    dataset_path: '',
    reference_modality: '',
    perform_alignment: true,
    alignment_force_recomputing: false,
    perform_registration: true,
    huggingface_token: null,
    spatial_annotations: null,
    modalities: [],
  };
}

function defaultPipelineStatus(): PipelineStatus {
  return {
    state: 'idle',
    stage: null,
    stage_index: 0,
    total_stages: 4,
    current_modality: null,
    current_modality_index: 0,
    total_modalities: 0,
    current_sample: null,
    current_sample_index: 0,
    total_samples: 0,
    message: '',
    error: null,
    output_files: [],
    alignment_port: 8000,
    sub_step: null,
    sub_step_index: 0,
    sub_step_total: 0,
    sub_step_progress: 0,
    sub_step_items_total: 0,
  };
}

export const useMainStore = defineStore('main', {
  state: () => ({
    schema: null as Schema | null,
    config: emptyConfig(),
    samples: [] as string[],
    currentView: 'setup' as 'setup' | 'config' | 'running' | 'complete',
    validationErrors: [] as string[],
    pipelineStatus: defaultPipelineStatus(),
    hasExistingConfig: false,
    isLoading: false,
    autoSaveTimeout: null as ReturnType<typeof setTimeout> | null,
    statusPollInterval: null as ReturnType<typeof setInterval> | null,
  }),

  getters: {
    modalityNames(): string[] {
      return this.config.modalities.map((m: Modality) => m.name).filter((n: string) => n.length > 0);
    },

    needsHuggingfaceToken(): boolean {
      return this.config.modalities.some(
        (m: Modality) => m.registration_type === 'feature_extraction'
      );
    },

    canStartPipeline(): boolean {
      return (
        this.config.dataset_path.length > 0 &&
        this.config.modalities.length > 0 &&
        this.config.reference_modality.length > 0 &&
        this.config.modalities.some((m: Modality) => m.name === this.config.reference_modality) &&
        this.validationErrors.length === 0
      );
    },
  },

  actions: {
    async fetchSchema() {
      this.schema = await api.getSchema();
    },

    async restoreState() {
      try {
        const state = await api.getState();

        // Restore config and samples
        if (state.config && state.config.dataset_path) {
          this.config = state.config as Config;
          this.samples = state.samples;
          this.hasExistingConfig = state.has_existing_config;
        }

        // Restore pipeline status and derive the correct view
        this.pipelineStatus = state.status;
        const s = state.status.state;
        if (s === 'running' || s === 'alignment_waiting') {
          this.currentView = 'running';
          this.startStatusPolling();
        } else if (s === 'completed') {
          this.currentView = 'complete';
        } else if (s === 'error') {
          this.currentView = 'running';  // RunningView shows the error UI
        } else if (state.config && state.config.modalities && state.config.modalities.length > 0) {
          this.currentView = 'config';
        }
        // else stay on 'setup' (the default)
      } catch {
        // Backend not reachable or fresh start — stay on setup
      }
    },

    async setDatasetPath(path: string) {
      this.config.dataset_path = path;
      try {
        const result = await api.getSamples(path);
        this.samples = result.samples;
        this.hasExistingConfig = result.has_existing_config;
      } catch {
        this.samples = [];
        this.hasExistingConfig = false;
      }
    },

    async loadExistingConfig(): Promise<{ success: boolean; corrupted?: boolean; errors?: string[] }> {
      // Pass dataset_path directly — do NOT call putConfig first, which would
      // overwrite the file on disk before we get a chance to read it.
      const result = await api.loadExistingConfig(this.config.dataset_path);
      if (result.valid && result.config) {
        this.config = result.config as Config;
        return { success: true };
      }
      return { success: false, corrupted: result.corrupted, errors: result.errors };
    },

    async loadConfigFromFile(content: string) {
      const result = await api.loadConfig({ content });
      if (result.valid && result.config) {
        this.config = result.config as Config;
        this.validationErrors = [];
        return true;
      } else {
        this.validationErrors = result.errors || ['Invalid configuration file'];
        return false;
      }
    },

    addModality(name: string) {
      this.config.modalities.push({
        name,
        type: this.schema?.modality_types[0] || '',
        processing_settings: {},
        registration_type: 'none',
        registration_settings: {},
        alignment_strategy: 'manual',
      });
      // Auto-select reference if none is set
      if (!this.config.reference_modality && name) {
        this.config.reference_modality = name;
      }
      this.triggerAutoSave();
    },

    removeModality(index: number) {
      const removed = this.config.modalities[index];
      this.config.modalities.splice(index, 1);
      // If removed was the reference, pick the next available or clear
      if (removed && removed.name === this.config.reference_modality) {
        const next = this.config.modalities.find((m: Modality) => m.name);
        this.config.reference_modality = next ? next.name : '';
      }
      this.triggerAutoSave();
    },

    removeAllModalities() {
      this.config.modalities = [];
      this.config.reference_modality = '';
      this.triggerAutoSave();
    },

    updateModality(index: number, updates: Partial<Modality>) {
      const m = this.config.modalities[index];
      if (m) {
        if (updates.name !== undefined) {
          // Keep reference_modality in sync when name changes
          if (m.name === this.config.reference_modality) {
            this.config.reference_modality = updates.name;
          }
          // Auto-select reference if none is set
          if (!this.config.reference_modality && updates.name) {
            this.config.reference_modality = updates.name;
          }
        }
        Object.assign(m, updates);
      }
      this.triggerAutoSave();
    },

    triggerAutoSave() {
      if (this.autoSaveTimeout) clearTimeout(this.autoSaveTimeout);
      this.autoSaveTimeout = setTimeout(() => this.autoSave(), 500);
    },

    async autoSave() {
      if (this.config.dataset_path) {
        await api.putConfig(this.config);
      }
    },

    async validate(): Promise<boolean> {
      // Save first so backend has latest
      await api.putConfig(this.config);
      const result = await api.validate();
      if (result.valid) {
        this.validationErrors = [];
        return true;
      } else {
        this.validationErrors = result.errors || ['Validation failed'];
        return false;
      }
    },

    async startPipeline() {
      this.isLoading = true;
      const valid = await this.validate();
      if (!valid) {
        this.isLoading = false;
        return;
      }

      try {
        await api.run();
        this.currentView = 'running';
        this.pipelineStatus = defaultPipelineStatus();
        this.pipelineStatus.state = 'running';
        this.startStatusPolling();
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : 'Failed to start pipeline';
        this.validationErrors = [msg];
      } finally {
        this.isLoading = false;
      }
    },

    startStatusPolling() {
      this.stopStatusPolling();
      this.statusPollInterval = setInterval(async () => {
        try {
          this.pipelineStatus = await api.getStatus();
          if (this.pipelineStatus.state === 'completed') {
            this.currentView = 'complete';
            this.stopStatusPolling();
          } else if (this.pipelineStatus.state === 'error') {
            this.stopStatusPolling();
          }
        } catch {
          // Keep polling on network hiccups
        }
      }, 1500);
    },

    stopStatusPolling() {
      if (this.statusPollInterval) {
        clearInterval(this.statusPollInterval);
        this.statusPollInterval = null;
      }
    },

    async resetAll() {
      this.stopStatusPolling();
      await api.reset();
      this.config = emptyConfig();
      this.samples = [];
      this.validationErrors = [];
      this.pipelineStatus = defaultPipelineStatus();
      this.hasExistingConfig = false;
      this.currentView = 'setup';
    },

    goToSetup() {
      this.currentView = 'setup';
    },

    goToConfig() {
      this.currentView = 'config';
    },
  },
});
