import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'

function resolveInitialTheme(): 'light' | 'dark' {
  try {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    if (typeof mq?.matches === 'boolean') return mq.matches ? 'dark' : 'light';
  } catch (_) { /* fall through */ }
  return 'dark';
}
const theme = resolveInitialTheme();
document.documentElement.classList.toggle('dark', theme === 'dark');

try {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener(
    'change',
    (e) => {
      if (!localStorage.getItem('focus-theme-override')) {
        document.documentElement.classList.toggle('dark', e.matches);
      }
    }
  );
} catch (_) {}

const pinia = createPinia()
const app = createApp(App)

app.use(pinia)
app.mount('#app')
