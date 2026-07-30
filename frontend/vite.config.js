import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite dev config. The proxy lets the SPA call the FastAPI backend (port 8000)
// using relative paths so api.js never needs an absolute base URL.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
});
