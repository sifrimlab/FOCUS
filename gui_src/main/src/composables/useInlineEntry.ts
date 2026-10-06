/**
 * State for an inline "type a name, then Add or Cancel" form.
 *
 * Validation and submission are injected. `validate` returns an error
 * message or null; `submit` may throw, and its error is shown inline.
 */
import { nextTick, ref } from 'vue';
import { errorMessage } from '../utils/errors';

export interface InlineEntryOptions {
  validate: (value: string) => string | null;
  submit: (value: string) => void | Promise<void>;
}

export function useInlineEntry({ validate, submit }: InlineEntryOptions) {
  const isOpen = ref(false);
  const value = ref('');
  const error = ref('');
  const busy = ref(false);
  const inputRef = ref<{ focus: () => void } | null>(null);

  const open = async () => {
    value.value = '';
    error.value = '';
    isOpen.value = true;
    await nextTick();
    inputRef.value?.focus();
  };

  const cancel = () => {
    isOpen.value = false;
  };

  const confirm = async () => {
    const name = value.value.trim();
    const invalid = validate(name);
    if (invalid) {
      error.value = invalid;
      return;
    }
    busy.value = true;
    error.value = '';
    try {
      await submit(name);
      isOpen.value = false;
    } catch (e: unknown) {
      error.value = errorMessage(e);
    } finally {
      busy.value = false;
    }
  };

  return { isOpen, value, error, busy, inputRef, open, cancel, confirm };
}

/** Common validator: non-empty and not already taken. */
export function uniqueNameValidator(existing: () => string[], noun: string) {
  return (name: string): string | null => {
    if (!name) return 'Please enter a name.';
    if (existing().includes(name)) return `A ${noun} with this name already exists.`;
    return null;
  };
}
