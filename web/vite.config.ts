import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
export default defineConfig(({ command, mode }) => {
  const root = fileURLToPath(new URL('.', import.meta.url));
  const env = loadEnv(mode, root, 'BACKEND_');
  const target = process.env.BACKEND_URL || env.BACKEND_URL;
  if (command === 'serve' && !target)
    throw new Error('Set BACKEND_URL to the remote CUDA showcase server.');
  return {
    root,
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5174,
      strictPort: true,
      proxy: target
        ? {
            '/api': { target, changeOrigin: true },
            '/ws': { target, changeOrigin: true, ws: true },
            '/media': { target, changeOrigin: true },
          }
        : undefined,
    },
    build: { outDir: 'dist', emptyOutDir: true },
    preview: { host: '127.0.0.1', port: 5174 },
  };
});
