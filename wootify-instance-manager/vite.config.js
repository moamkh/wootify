import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ command }) => {
  const apiTarget = process.env.VITE_API_BASE || 'http://localhost:8000';
  return {
    plugins: [react()],
    base: command === 'build' ? '/instance-manager/' : '/',
    server: {
      port: 5173,
      proxy: {
        '/api': apiTarget,
        '/health': apiTarget,
      },
    }
  };
});
