<script setup lang="ts">
/**
 * Floating top-right glass pill: project links and the theme control
 * (DESIGN.md 8.11). `collapsible` folds it into a three-dots button that
 * expands in place and stays open until a press outside it or Escape.
 */
import { computed, ref } from 'vue';
import ChromeSurface from '../ui/ChromeSurface.vue';
import IconButton from '../ui/IconButton.vue';
import Separator from '../ui/Separator.vue';
import ThemeSwitcher from './ThemeSwitcher.vue';
import { useDismiss } from '../../composables/useDismiss';
import type { IconName } from '../../icons/paths';

const props = defineProps<{ collapsible?: boolean }>();

const LINKS: { href: string; label: string; icon: IconName }[] = [
  { href: 'https://github.com/sifrimlab/FOCUS', label: 'GitHub repository', icon: 'github' },
  { href: 'https://sifrimlab.org/FOCUS/', label: 'Documentation', icon: 'book-open' },
  { href: 'https://doi.org/10.64898/2026.08.04.742705', label: 'Scientific paper', icon: 'academic-cap' },
];

const root = ref<HTMLElement | null>(null);
const open = ref(!props.collapsible);
// Only a collapsible cluster listens for outside presses.
useDismiss([root], computed(() => !!props.collapsible && open.value), () => { open.value = false; });
</script>

<template>
  <div ref="root" class="fixed top-hud right-hud z-chrome">
    <ChromeSurface as="nav" aria-label="Project links and appearance">
      <Transition name="island" mode="out-in">
        <div v-if="open" class="flex origin-right items-center gap-0.5">
          <IconButton v-for="link in LINKS" :key="link.href" v-bind="link" round />
          <Separator />
          <ThemeSwitcher />
        </div>
        <IconButton
          v-else
          icon="ellipsis-horizontal"
          label="Links and appearance"
          round
          :aria-expanded="false"
          @click="open = true"
        />
      </Transition>
    </ChromeSurface>
  </div>
</template>
