import axios from 'axios';
import type { SampleStatus, Metadata } from './types';

const apiClient = axios.create({
  // Empty baseURL means requests will be relative to the current origin (e.g. http://localhost:8000)
  baseURL: '', 
  headers: {
    'Content-Type': 'application/json',
  },
});

export const api = {
  async getStatus(): Promise<SampleStatus | null> {
    const response = await apiClient.get<SampleStatus>('/status');
    return response.data;
  },

  async getMetadata(type: 'reference' | 'target'): Promise<Metadata> {
    const response = await apiClient.get<Metadata>(`/${type}/metadata`);
    return response.data;
  },

  async getPayload(type: 'reference' | 'target', responseType: 'blob' | 'json' = 'json'): Promise<any> {
    const response = await apiClient.get(`/${type}/payload`, { responseType });
    return response.data;
  },

  async confirm(payload: any): Promise<void> {
    await apiClient.post('/confirm', payload);
  },
};

export async function pollStatus(): Promise<SampleStatus | null> {
  let delay = 1000;
  while (true) {
    try {
      const response = await apiClient.get<SampleStatus>('/status');
      return response.data;
    } catch (error: any) {
      if (error.response) {
        if (error.response.status === 404) {
          return null; // All samples processed
        }
        if (error.response.status === 400) {
          // Sample not ready, retry
          console.log(`Sample not ready, retrying in ${delay}ms...`);
          await new Promise(resolve => setTimeout(resolve, delay));
          delay = Math.min(delay * 1.5, 10000); // Exponential back-off capped at 10s
          continue;
        }
        if (error.response.status === 500) {
          // Alignment thread error — extract message and re-throw
          const msg = error.response.data?.error || 'Alignment backend error';
          throw new Error(msg);
        }
      }
      throw error;
    }
  }
}
