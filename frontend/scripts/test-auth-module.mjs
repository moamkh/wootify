import assert from 'node:assert/strict';
import { build } from 'esbuild';

const storage = new Map();

globalThis.localStorage = {
  getItem: (key) => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
};
globalThis.window = {
  dispatchEvent() {},
  location: { hostname: 'wootify.example.test' },
};
globalThis.CustomEvent = class CustomEvent {};
let requestedUrl = '';
globalThis.fetch = async (url) => {
  requestedUrl = String(url);
  return {
    ok: true,
    async json() {
      return { token: 'auth-regression-token' };
    },
  };
};

const bundle = await build({
  entryPoints: ['src/shared/api/auth.js'],
  bundle: true,
  write: false,
  format: 'esm',
  define: {
    'import.meta.env.VITE_API_BASE': JSON.stringify('http://localhost:8000'),
  },
});
const source = bundle.outputFiles[0].text;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const auth = await import(moduleUrl);

await auth.panelLogin('test-user', 'test-password');

assert.equal(auth.getPanelToken(), 'auth-regression-token');
assert.equal(requestedUrl, '/api/v1/panel/auth/login');
console.log('panelLogin success path stores the returned panel token');
console.log('remote deployment ignores a localhost API build setting');
