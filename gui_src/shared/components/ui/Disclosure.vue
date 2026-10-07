<script setup lang="ts">
/**
 * Collapsible section on native <details> (DESIGN.md 8.4). Summary in
 * Headline (or Footnote for `compact`) with a rotating chevron. Content
 * height animates where `interpolate-size` is supported.
 * `v-model:open` keeps the state outside (it survives remounts); the
 * `summary` slot replaces the title text with richer content.
 */
import AppIcon from './AppIcon.vue';

const open = defineModel<boolean>('open', { default: false });

defineProps<{
  title: string;
  compact?: boolean;
}>();

const sync = (e: Event) => { open.value = (e.target as HTMLDetailsElement).open; };
</script>

<template>
  <details class="disclosure" :open="open" @toggle="sync">
    <summary
      class="disclosure__summary flex items-center gap-1.5 select-none"
      :class="compact ? 'type-footnote text-fg3' : 'type-headline text-fg1'"
    >
      <AppIcon name="chevron-down" :size="compact ? 12 : 16" class="disclosure__chevron text-fg3" />
      <slot name="summary"><span>{{ title }}</span></slot>
    </summary>
    <div :class="compact ? 'pt-1.5' : 'pt-3'">
      <slot />
    </div>
  </details>
</template>

<style scoped>
.disclosure__summary { list-style: none; width: fit-content; }
.disclosure__summary::-webkit-details-marker { display: none; }
.disclosure__summary:hover { color: var(--fg1); }

.disclosure__chevron {
  transform: rotate(-90deg);
  transition: transform var(--dur-base) var(--ease-spring);
}
.disclosure[open] > .disclosure__summary .disclosure__chevron { transform: rotate(0deg); }

@supports (interpolate-size: allow-keywords) {
  .disclosure { interpolate-size: allow-keywords; }
  .disclosure::details-content {
    height: 0;
    overflow: clip;
    transition: height var(--dur-base) var(--ease-out), content-visibility var(--dur-base) allow-discrete;
  }
  .disclosure[open]::details-content { height: auto; }
}
</style>
