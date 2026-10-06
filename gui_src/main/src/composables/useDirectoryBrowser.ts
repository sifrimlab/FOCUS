/**
 * State for navigating a server-side directory listing.
 *
 * The fetcher is injected, so the same logic serves the folder-only browser
 * (api.browse) and the file browser (api.browseFiles).
 */
import { ref, type Ref } from 'vue';
import { errorMessage } from '../utils/errors';

export interface DirectoryEntry {
  name: string;
  is_dir?: boolean;
}

export interface DirectoryListing<E extends DirectoryEntry> {
  path: string;
  parent: string | null;
  entries: E[];
}

export function useDirectoryBrowser<E extends DirectoryEntry>(
  fetcher: (path: string) => Promise<DirectoryListing<E>>,
) {
  const path = ref('');
  const parent = ref<string | null>(null);
  const entries = ref([]) as Ref<E[]>;
  const loading = ref(false);
  const error = ref('');

  /** Load a directory. Resolves to the resolved path, or null on failure. */
  const browseTo = async (target: string): Promise<string | null> => {
    loading.value = true;
    error.value = '';
    try {
      const result = await fetcher(target);
      path.value = result.path;
      parent.value = result.parent;
      entries.value = result.entries;
      return result.path;
    } catch (e: unknown) {
      error.value = errorMessage(e, 'Could not browse directory');
      return null;
    } finally {
      loading.value = false;
    }
  };

  const goUp = () => (parent.value === null ? Promise.resolve(null) : browseTo(parent.value));

  const childPath = (name: string) => `${path.value}/${name}`;

  return { path, parent, entries, loading, error, browseTo, goUp, childPath };
}
