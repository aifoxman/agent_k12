import { defineConfig } from 'vite';

export default defineConfig({
  base: '/app/',
  publicDir: '../../assets',
  esbuild: { jsx: 'automatic' },
});
