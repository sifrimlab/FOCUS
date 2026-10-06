<script setup lang="ts">
/**
 * On/off chip (DESIGN.md 8.6). On: tinted with a check. Off: outlined and
 * struck through. State is carried by fill, icon and strike, not hue alone.
 * `size="lg"` renders a full-width tile for grids.
 */
import AppIcon from './AppIcon.vue';

const model = defineModel<boolean>({ default: false });

withDefaults(defineProps<{ size?: 'sm' | 'lg' }>(), { size: 'sm' });
</script>

<template>
  <button
    type="button"
    class="chip"
    :class="size === 'lg' ? 'chip--lg type-mono' : 'type-mono-small'"
    :aria-pressed="model"
    @click="model = !model"
  >
    <AppIcon v-if="model" name="check" :size="size === 'lg' ? 16 : 12" />
    <span class="chip__label ellipsis"><slot /></span>
  </button>
</template>

<style scoped>
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: var(--control-sm);
  padding: 0 10px;
  border-radius: var(--radius-full);
  color: var(--fg3);
  box-shadow: inset 0 0 0 1px var(--separator-strong);
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out),
    transform var(--dur-base) var(--ease-spring-snappy);
}
.chip--lg {
  width: 100%;
  height: var(--control-xl);
  gap: 8px;
  padding: 0 14px;
  border-radius: var(--radius-lg);
}
.chip:hover { background: var(--mat-inset-fill); }
.chip:active { transform: scale(var(--press-scale)); transition-duration: var(--dur-instant); }
.chip[aria-pressed="false"] .chip__label { text-decoration: line-through; }

.chip[aria-pressed="true"] {
  background: var(--primary-soft);
  color: var(--primary-fg);
  box-shadow: none;
}
.chip[aria-pressed="true"]:hover { background: color-mix(in srgb, var(--primary-soft), var(--primary) 10%); }
</style>
