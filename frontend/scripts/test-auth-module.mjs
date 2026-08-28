import assert from 'node:assert/strict';
import { build } from 'esbuild';

const storage = new Map();

globalThis.localStorage = {
  getItem: (key) => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
};
globalThis.window = { dispatchEvent() {} };
globalThis.CustomEvent = class CustomEvent {};
globalThis.fetch = async () => ({
  ok: true,
  async json() {
    return { token: 'auth-regression-token' };
  },
});

const bundle = await build({
  entryPoints: ['src/shared/api/auth.js'],
  bundle: true,
  write: false,
  format: 'esm',
  define: {
    'import.meta.env.VITE_API_BASE': JSON.stringify(''),
  },
});
const source = bundle.outputFiles[0].text;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const auth = await import(moduleUrl);

await auth.panelLogin('test-user', 'test-password');

assert.equal(auth.getPanelToken(), 'auth-regression-token');
console.log('panelLogin success path stores the returned panel token');
