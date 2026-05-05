import { reactive } from 'vue';

export interface DialogOptions {
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: 'danger' | 'default';
}

interface DialogState {
  visible: boolean;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  variant: 'danger' | 'default';
  resolve: ((value: boolean) => void) | null;
}

// Module-level singleton — shared across all components
const state = reactive<DialogState>({
  visible: false,
  message: '',
  confirmLabel: 'Confirm',
  cancelLabel: 'Cancel',
  variant: 'default',
  resolve: null,
});

export function useDialog() {
  const showConfirm = (options: DialogOptions | string): Promise<boolean> => {
    const opts = typeof options === 'string' ? { message: options } : options;
    state.message = opts.message;
    state.confirmLabel = opts.confirmLabel ?? 'Confirm';
    state.cancelLabel = opts.cancelLabel ?? 'Cancel';
    state.variant = opts.variant ?? 'default';
    state.visible = true;
    return new Promise<boolean>((resolve) => {
      state.resolve = resolve;
    });
  };

  const handleConfirm = () => {
    state.visible = false;
    state.resolve?.(true);
    state.resolve = null;
  };

  const handleCancel = () => {
    state.visible = false;
    state.resolve?.(false);
    state.resolve = null;
  };

  return { state, showConfirm, handleConfirm, handleCancel };
}