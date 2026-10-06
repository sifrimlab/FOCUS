<script setup lang="ts">
/** Logo mark + wordmark, switching the mark variant with the theme. */
import { computed } from 'vue';
import BrandWordmark from './BrandWordmark.vue';
import { useTheme } from '../../composables/useTheme';
import logoMark from '../../assets/logo-mark.svg';
import logoMarkDark from '../../assets/logo-mark-dark.svg';

const { isDark } = useTheme();
const markSrc = computed(() => (isDark.value ? logoMarkDark : logoMark));
</script>

<template>
  <div class="flex items-center justify-center gap-3">
    <img :src="markSrc" alt="" width="48" height="48" class="shrink-0" />
    <BrandWordmark />
  </div>
</template>
