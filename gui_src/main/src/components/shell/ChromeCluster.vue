<script setup lang="ts">
/** Floating top-right glass pill: project links and the theme control. */
import IconButton from '../ui/IconButton.vue';
import ThemeSwitcher from './ThemeSwitcher.vue';
import type { IconName } from '../../icons/paths';

const LINKS: { href: string; label: string; icon: IconName }[] = [
  { href: 'https://github.com/sifrimlab/FOCUS', label: 'GitHub repository', icon: 'github' },
  { href: 'https://sifrimlab.org/FOCUS/', label: 'Documentation', icon: 'book-open' },
  { href: 'https://doi.org/10.64898/2026.08.04.742705', label: 'Scientific paper', icon: 'academic-cap' },
];
</script>

<template>
  <nav class="material-chrome fixed top-4 right-4 z-50 flex items-center gap-0.5 rounded-full p-1" aria-label="Project links and appearance">
    <IconButton v-for="link in LINKS" :key="link.href" v-bind="link" round />
    <span class="mx-1 h-4 w-px bg-separator-strong" aria-hidden="true" />
    <ThemeSwitcher />
  </nav>
</template>
