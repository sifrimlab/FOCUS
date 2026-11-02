import { focusViteConfig } from '../shared/vite.base';

export default focusViteConfig({
  outDir: '../../src/focus/gui/main/',
  proxy: { '/api': 'http://localhost:5050' },
});
