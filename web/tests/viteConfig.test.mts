import assert from 'node:assert/strict';
import { copyFile, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

test('Vite uses the local backend by default and preserves explicit deployment overrides', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'showcase-vite-'));
  const previous = {
    BACKEND_URL: process.env.BACKEND_URL,
    LIVE_BACKEND_URL: process.env.LIVE_BACKEND_URL,
  };
  delete process.env.BACKEND_URL;
  delete process.env.LIVE_BACKEND_URL;
  try {
    await copyFile(new URL('../vite.config.ts', import.meta.url), join(root, 'vite.config.ts'));
    await symlink(
      fileURLToPath(new URL('../../node_modules', import.meta.url)),
      join(root, 'node_modules'),
      'dir',
    );
    const { default: config } = await import(pathToFileURL(join(root, 'vite.config.ts')).href);
    const resolve = () => config({ command: 'serve', mode: 'test' });

    await t.test('HTTP, media and WebSocket use loopback without an env file', () => {
      const result = resolve();
      for (const path of ['/api', '/media', '/ws'])
        assert.equal(result.server.proxy[path].target, 'http://127.0.0.1:8010');
      assert.equal(result.server.host, '127.0.0.1');
      assert.equal(result.server.proxy['/ws'].ws, true);
      assert.equal(result.server.proxy['/api/multiview/live'], undefined);
    });

    await t.test('an explicit env file can still select a remote CUDA host', async () => {
      await writeFile(join(root, '.env.local'), 'BACKEND_URL=http://cuda.example:8010\n');
      assert.equal(resolve().server.proxy['/api'].target, 'http://cuda.example:8010');
    });

    await t.test('shell configuration wins and live-input override remains scoped', () => {
      process.env.BACKEND_URL = 'http://127.0.0.1:8020';
      process.env.LIVE_BACKEND_URL = 'http://127.0.0.1:8000';
      const result = resolve();
      assert.equal(result.server.proxy['/api'].target, 'http://127.0.0.1:8020');
      assert.equal(result.server.proxy['/ws'].target, 'http://127.0.0.1:8020');
      assert.equal(result.server.proxy['/api/multiview/live'].target, 'http://127.0.0.1:8000');
    });
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(root, { recursive: true, force: true });
  }
});
