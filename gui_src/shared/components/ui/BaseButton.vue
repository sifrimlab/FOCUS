<script setup lang="ts">
/**
 * Text button (DESIGN.md 8.1). Variants pick color, sizes pick height.
 * Primary and success default to the pill shape; others are rounded.
 * Native button attributes and listeners pass through.
 */
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';
import type { IconName } from '../../icons/paths';

export type ButtonVariant =
  | 'primary' | 'success' | 'secondary' | 'tinted' | 'plain' | 'destructive' | 'destructive-filled';

const props = withDefaults(defineProps<{
  variant?: ButtonVariant;
  size?: 'md' | 'lg' | 'xl';
  shape?: 'pill' | 'rounded';
  icon?: IconName;
  block?: boolean;
  type?: 'button' | 'submit';
}>(), { variant: 'secondary', size: 'md', type: 'button' });

const resolvedShape = computed(() =>
  props.shape ?? (props.variant === 'primary' || props.variant === 'success' ? 'pill' : 'rounded'),
);
</script>

<template>
  <button
    :type="type"
    class="btn"
    :class="[`btn--${variant}`, `btn--${size}`, `btn--${resolvedShape}`, { 'btn--block': block }]"
  >
    <AppIcon v-if="icon" :name="icon" :size="size === 'xl' ? 20 : 16" />
    <slot />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: var(--control-md);
  padding: 0 14px;
  font: var(--type-button);
  letter-spacing: var(--track-button);
  white-space: nowrap;
  user-select: none;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out),
    transform var(--dur-base) var(--ease-spring-snappy);
}
.btn:active:not(:disabled) {
  transform: scale(var(--press-scale));
  transition-duration: var(--dur-instant);
}
.btn:disabled { opacity: 0.4; cursor: not-allowed; }

.btn--lg { height: var(--control-lg); padding: 0 18px; }
.btn--xl {
  height: var(--control-xl);
  padding: 0 24px;
  font: var(--type-button-lg);
  letter-spacing: var(--track-button-lg);
}
.btn--block { display: flex; width: 100%; }
.btn--pill { border-radius: var(--radius-full); }
.btn--rounded { border-radius: var(--radius-md); }

.btn--primary { background: var(--primary-fill); color: var(--fg-inverse); box-shadow: var(--fill-highlight), var(--elev-1); }
.btn--primary:hover:not(:disabled) { background: var(--primary-hover); }
.btn--primary:active:not(:disabled) { background: var(--primary-press); }

.btn--success { background: var(--success-fill); color: var(--fg-inverse); box-shadow: var(--fill-highlight), var(--elev-1); }
.btn--success:hover:not(:disabled) { background: var(--success-hover); }

.btn--destructive-filled { background: var(--danger-fill); color: var(--fg-inverse); box-shadow: var(--fill-highlight), var(--elev-1); }
.btn--destructive-filled:hover:not(:disabled) { background: var(--danger-hover); }

.btn--secondary { background: var(--mat-inset-fill); color: var(--fg1); }
.btn--secondary:hover:not(:disabled) { background: var(--mat-inset-hover); }

.btn--tinted { background: var(--primary-soft); color: var(--primary-fg); }
.btn--tinted:hover:not(:disabled) { background: color-mix(in srgb, var(--primary-soft), var(--primary) 10%); }

.btn--plain { background: transparent; color: var(--primary-fg); }
.btn--plain:hover:not(:disabled) { background: var(--mat-inset-fill); }

.btn--destructive { background: var(--danger-soft); color: var(--danger-fg); }
.btn--destructive:hover:not(:disabled) { background: color-mix(in srgb, var(--danger-soft), var(--danger) 10%); }
</style>
