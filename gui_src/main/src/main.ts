import { createApp } from 'vue';
import { createPinia } from 'pinia';
import '@fontsource-variable/inter/opsz.css';
import '@fontsource-variable/jetbrains-mono/wght.css';
import './styles/index.css';
import App from './App.vue';
import { initTheme } from './composables/useTheme';

// Resolve the theme before mounting so the first paint is correct.
initTheme();

createApp(App).use(createPinia()).mount('#app');
