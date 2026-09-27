import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// Built to static assets at image-build time and served by Caddy.
// There is no Node runtime in production (F14.AC4, ADR-0003).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Fail the build rather than ship something that will be slow on a shared
    // vCPU. ECharts arrives in M5 and must be imported per-module, never as the
    // barrel export, or this ceiling is hit immediately (ADR-0003).
    chunkSizeWarningLimit: 600,
  },
  server: {
    host: true,
    port: 5173,
    // Dev-only convenience so `vite dev` can reach the API without Caddy.
    // Production traffic never uses this: Caddy routes /api, /r and the probes.
    proxy: {
      '/api': { target: 'http://api:8000', changeOrigin: true },
      '/healthz': { target: 'http://api:8000', changeOrigin: true },
      '/readyz': { target: 'http://api:8000', changeOrigin: true },
    },
  },
});
