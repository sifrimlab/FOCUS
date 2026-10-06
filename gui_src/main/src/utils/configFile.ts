/** Reading and summarizing a user-supplied focus_config.json before it is applied. */

export type ConfigFileResult =
  | { name: string; data: Record<string, unknown> }
  | { name: string; error: string };

/** Parse a .json file into an object, with messages a user can act on. */
export async function readConfigFile(file: File): Promise<ConfigFileResult> {
  const name = file.name;
  if (!name.toLowerCase().endsWith('.json')) {
    return { name, error: 'Please choose a .json file.' };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await file.text());
  } catch (e: unknown) {
    return { name, error: `The file is not valid JSON: ${e instanceof Error ? e.message : String(e)}` };
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { name, error: 'The file does not contain a configuration object.' };
  }
  return { name, data: parsed as Record<string, unknown> };
}

export interface ConfigSummary {
  datasetPath: string;
  reference: string;
  modalities: { name: string; type: string }[];
  alignment: boolean;
  registration: boolean;
  annotations: boolean;
  ignoredSamples: number;
}

const asString = (v: unknown) => (typeof v === 'string' ? v : '');
const asArray = (v: unknown) => (Array.isArray(v) ? v : []);

/** Key facts of a raw config object. Missing or malformed fields read as empty. */
export function summarizeConfig(data: Record<string, unknown>): ConfigSummary {
  return {
    datasetPath: asString(data.dataset_path),
    reference: asString(data.reference_modality),
    modalities: asArray(data.modalities).map(m => {
      const mod = (m ?? {}) as Record<string, unknown>;
      return { name: asString(mod.name), type: asString(mod.type) };
    }),
    alignment: data.perform_alignment !== false,
    registration: data.perform_registration !== false,
    annotations: data.spatial_annotations !== null && data.spatial_annotations !== undefined,
    ignoredSamples: asArray(data.ignore_samples).length,
  };
}
