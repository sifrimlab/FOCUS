import { defineStore } from 'pinia';
import { api, pollStatus } from '../api/client';
import type { SampleStatus, Metadata, SpotModalityPayload } from '../api/types';
import { mat3 } from 'gl-matrix';
import { createIdentity } from '../utils/matrix';
import { computeExportPayload } from '../utils/export';

export const useMainStore = defineStore('main', {
  state: () => ({
    sampleInfo: null as SampleStatus | null,
    referenceMeta: null as Metadata | null,
    targetMeta: null as Metadata | null,
    referenceData: null as Blob | SpotModalityPayload | null,
    targetData: null as Blob | SpotModalityPayload | null,
    targetTransform: createIdentity(),
    referenceTransform: createIdentity(),
    isLoading: false,
    isFinished: false,
    isBackendDown: false,
    error: null as string | null,
    referenceSpotClasses: [] as number[],
    referenceClassFilter: [] as number[],
    targetSpotClasses: [] as number[],
    targetClassFilter: [] as number[],
    targetOpacity: 0.7,
    globalZoom: 1.0,
    viewOffset: [0, 0] as [number, number],
    pendingCommand: null as { type: 'zoom' | 'rotate' | 'flip' | 'reset' | 'setScale' | 'setRotation' | 'resetScale' | 'resetRotation', value?: any } | null,
    referenceSpotBoost: 1.0,
    targetSpotBoost: 1.0,
    controlMode: 'aligner' as 'aligner' | 'camera',
    alignerInteraction: 'translate' as 'translate' | 'rotate' | 'distort',
    referenceSpotSize: [1, 1] as [number, number],
    targetSpotSize: [1, 1] as [number, number],
    referenceForegroundMode: 'all' as 'all' | 'foreground' | 'background',
    targetForegroundMode: 'all' as 'all' | 'foreground' | 'background',
    loadingMessage: null as string | null,
  }),
  getters: {
    commonSpotBoost(state): number {
        return Math.max(state.referenceSpotBoost, state.targetSpotBoost);
    }
  },
  actions: {
    setControlMode(mode: 'aligner' | 'camera') {
        this.controlMode = mode;
    },
    setAlignerInteraction(mode: 'translate' | 'rotate' | 'distort') {
        this.alignerInteraction = mode;
    },
    setReferenceSpotBoost(val: number) {
        if (Math.abs(this.referenceSpotBoost - val) > 0.0001) {
            this.referenceSpotBoost = val;
        }
    },
    setTargetSpotBoost(val: number) {
        if (Math.abs(this.targetSpotBoost - val) > 0.0001) {
            this.targetSpotBoost = val;
        }
    },
    updateViewOffset(dx: number, dy: number) {
        // Adjust speed based on zoom level: faster when zoomed out
        const speedFactor = 1.0 / this.globalZoom;
        this.viewOffset = [
            this.viewOffset[0] + dx * speedFactor, 
            this.viewOffset[1] + dy * speedFactor
        ];
    },
    async fetchNextSample() {
      this.isLoading = true;
      this.error = null;
      try {
        const status = await pollStatus();
        if (!status) {
          this.isFinished = true;
          this.isLoading = false;
          return;
        }
        this.sampleInfo = status;

        // Fetch metadata
        const [refMeta, tgtMeta] = await Promise.all([
          api.getMetadata('reference'),
          api.getMetadata('target'),
        ]);
        this.referenceMeta = refMeta;
        this.targetMeta = tgtMeta;

        if (refMeta.spot_size) {
            this.referenceSpotSize = [...refMeta.spot_size];
        }
        if (tgtMeta.spot_size) {
            this.targetSpotSize = [...tgtMeta.spot_size];
        }

        // Fetch payloads
        const [refData, tgtData] = await Promise.all([
          api.getPayload('reference', refMeta.modality_type === 'IMAGE' ? 'blob' : 'json'),
          api.getPayload('target', tgtMeta.modality_type === 'IMAGE' ? 'blob' : 'json'),
        ]);
        this.referenceData = refData;
        this.targetData = tgtData;

        // Reset transform
        this.targetTransform = createIdentity();
        this.referenceTransform = createIdentity(); // Will be updated by UI when rendered
        this.globalZoom = 1.0;
        this.viewOffset = [0, 0];
        this.referenceForegroundMode = 'all';
        this.targetForegroundMode = 'all';

        // Initialize filters if spot
        this.referenceSpotClasses = [];
        this.referenceClassFilter = [];
        this.targetSpotClasses = [];
        this.targetClassFilter = [];
        
        const extractClasses = (data: any, meta: Metadata) => {
            if (meta.modality_type === 'SPOT') {
                const spots = data as SpotModalityPayload;
                const classes = new Set(spots.map(s => s.class));
                return Array.from(classes).sort((a, b) => a - b);
            }
            return [];
        };

        if (refMeta.modality_type === 'SPOT') {
             const refClasses = extractClasses(refData, refMeta);
             this.referenceSpotClasses = refClasses;
             this.referenceClassFilter = [...refClasses];
        }
        if (tgtMeta.modality_type === 'SPOT') {
             const tgtClasses = extractClasses(tgtData, tgtMeta);
             this.targetSpotClasses = tgtClasses;
             this.targetClassFilter = [...tgtClasses];
        }

      } catch (e: any) {
        console.error(e);
        // Check for 404 or 400 which might indicate end of dataset
        if (e.response && (e.response.status === 404 || e.response.status === 400)) {
             this.isFinished = true;
             this.isLoading = false;
             return;
        }

        if (e.code === 'ERR_NETWORK' || e.message === 'Network Error' || (e.isAxiosError && !e.response)) {
            this.isBackendDown = true;
        } else {
            this.error = e.message || 'Failed to load sample';
        }
      } finally {
        this.isLoading = false;
      }
    },

    updateTargetTransform(newMatrix: mat3) {
        this.targetTransform = newMatrix;
    },

    updateReferenceTransform(newMatrix: mat3) {
        this.referenceTransform = newMatrix;
    },

    async confirm() {
        if (!this.referenceMeta || !this.targetMeta || !this.referenceData || !this.targetData) return;
        
        try {
            this.isLoading = true;
            this.loadingMessage = "Transformation saved. Waiting for backend processing...";
            
            const payload = computeExportPayload(
                this.referenceMeta,
                this.targetMeta,
                this.referenceData,
                this.targetData,
                this.targetTransform,
                this.referenceTransform
            );
            await api.confirm(payload);
            
            this.loadingMessage = null; // Reset to default loading message
            // On success, fetch next
            await this.fetchNextSample();
        } catch (e: any) {
            console.error(e);
            this.isLoading = false;
            this.loadingMessage = null;
            // Show error toast (handled by UI observing error state or return false)
            this.error = e.message || 'Failed to confirm alignment';
        }
    }
  }
});
