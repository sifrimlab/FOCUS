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
  }),
  actions: {
    updateViewOffset(dx: number, dy: number) {
        this.viewOffset = [this.viewOffset[0] + dx, this.viewOffset[1] + dy];
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
            const payload = computeExportPayload(
                this.referenceMeta,
                this.targetMeta,
                this.referenceData,
                this.targetData,
                this.targetTransform,
                this.referenceTransform
            );
            await api.confirm(payload);
            // On success, fetch next
            await this.fetchNextSample();
        } catch (e: any) {
            console.error(e);
            // Show error toast (handled by UI observing error state or return false)
            this.error = e.message || 'Failed to confirm alignment';
        }
    }
  }
});
