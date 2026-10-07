<script setup lang="ts">
/** FOCUS logo mark, switching its variant with the theme (DESIGN.md 1.6: fixed branding). */
import { computed } from 'vue';
import { useTheme } from '../../composables/useTheme';
import logoMark from '../../assets/logo-mark.svg';
import logoMarkDark from '../../assets/logo-mark-dark.svg';

withDefaults(defineProps<{ size?: number }>(), { size: 48 });

const { isDark } = useTheme();
const src = computed(() => (isDark.value ? logoMarkDark : logoMark));
</script>

<template>
  <img :src="src" alt="" :width="size" :height="size" class="shrink-0" />
</template>
