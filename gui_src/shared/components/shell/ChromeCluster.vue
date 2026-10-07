<script setup lang="ts">
/** Floating top-right glass pill: project links and the theme control. */
import ChromeSurface from '../ui/ChromeSurface.vue';
import IconButton from '../ui/IconButton.vue';
import Separator from '../ui/Separator.vue';
import ThemeSwitcher from './ThemeSwitcher.vue';
import type { IconName } from '../../icons/paths';

const LINKS: { href: string; label: string; icon: IconName }[] = [
  { href: 'https://github.com/sifrimlab/FOCUS', label: 'GitHub repository', icon: 'github' },
  { href: 'https://sifrimlab.org/FOCUS/', label: 'Documentation', icon: 'book-open' },
  { href: 'https://doi.org/10.64898/2026.08.04.742705', label: 'Scientific paper', icon: 'academic-cap' },
];
</script>

<template>
  <ChromeSurface as="nav" class="fixed top-hud right-hud z-chrome flex items-center gap-0.5" aria-label="Project links and appearance">
    <IconButton v-for="link in LINKS" :key="link.href" v-bind="link" round />
    <Separator />
    <ThemeSwitcher />
  </ChromeSurface>
</template>
