import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const backendOrigin = process.env.FIGURE_STUDIO_BACKEND || 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    // Built bundle is committed in the Python package so pip-installed users get it.
    outDir: path.resolve(__dirname, '../src/figure_studio/static'),
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2020',
  },
  server: {
    port: 5173,
    proxy: {
      '/api':     { target: backendOrigin, changeOrigin: true, ws: false },
      '/ws':      { target: backendOrigin, changeOrigin: true, ws: true },
      '/healthz': { target: backendOrigin, changeOrigin: true },
    },
  },
});
