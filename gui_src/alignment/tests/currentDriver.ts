/** Selects the driver for the GUI currently in src/ (the goldens were recorded with the legacy one). */
import type { Pinia } from 'pinia';
import { workspaceDriver } from './harness/workspaceDriver';

export async function makeDriver() {
  return (pinia: Pinia) => workspaceDriver(pinia);
}
