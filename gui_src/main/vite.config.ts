import { focusViteConfig } from '../shared/vite.base';

export default focusViteConfig({
  outDir: '../../src/focus/GUI/main/',
  proxy: { '/api': 'http://localhost:5050' },
});
