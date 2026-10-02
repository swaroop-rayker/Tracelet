import type { ProxyOptions } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

/**
 * Where the dev server sends API calls.
 *
 * Default: the `api` container, for `vite dev` run inside the compose network.
 * `--mode host` (`npm run dev:host`): the running stack's Caddy at https://localhost,
 * for a dev server on the host -- Caddy's local CA is not trusted by every browser, so
 * this lets one look at the dashboard over http://localhost:5173 against the real API.
 * The Origin header is rewritten to the site's own, which is what the API's CSRF origin
 * check expects from a same-origin browser (F8.AC11). Dev only: production has no Node
 * runtime and no proxy (F14.AC4).
 */
function apiProxy(mode: string): Record<string, ProxyOptions> {
  const target = mode === 'host' ? 'https://localhost' : 'http://api:8000';
  const options: ProxyOptions = {
    target,
    changeOrigin: true,
    secure: false, // Caddy's internal CA on localhost; never used outside dev
    ...(mode === 'host' ? { headers: { Origin: 'https://localhost' } } : {}),
  };
  return { '/api': options, '/healthz': options, '/readyz': options };
}

// Built to static assets at image-build time and served by Caddy.
// There is no Node runtime in production (F14.AC4, ADR-0003).
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Fail the build rather than ship something that will be slow on a shared
    // vCPU. ECharts is imported per-module, never as the barrel export, or this
    // ceiling is hit immediately (ADR-0003); the map and charts are split into
    // their own chunks by the router's lazy pages.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        // zrender is ECharts' renderer, a separate library: its own chunk, fetched in
        // parallel with ECharts on the first chart page, and cached independently.
        manualChunks: (id: string): string | undefined =>
          id.includes('node_modules/zrender/') ? 'zrender' : undefined,
      },
    },
  },
  test: {
    // Node, not a DOM: components are tested by rendering to static markup, which is
    // what "no panel can render blank" (F9.AC18) needs, without jsdom (ES5).
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
  server: {
    host: true,
    port: 5173,
    // Dev-only convenience so `vite dev` can reach the API without Caddy.
    // Production traffic never uses this: Caddy routes /api, /r and the probes.
    proxy: apiProxy(mode),
  },
}));
