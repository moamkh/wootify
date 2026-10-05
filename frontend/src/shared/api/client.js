/** Shared API transport, URL, and panel-token primitives. */
// Production builds are served by the Wootify backend. A relative base keeps
// API calls on that same origin when VITE_API_BASE is omitted. It also protects
// deployments accidentally built with a developer's localhost .env value:
// localhost remains valid in local development, but is never used by a remote
// browser session.
const configuredApiBase = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
const configuredForLoopback = /^https?:\/\/(localhost|127\.0\.0\.1)(?::|\/|$)/i.test(configuredApiBase);
const browserIsLoopback = typeof window !== 'undefined'
  && /^(localhost|127\.0\.0\.1)$/i.test(window.location.hostname);

export const API_BASE = configuredForLoopback && !browserIsLoopback ? '' : configuredApiBase;

const TOKEN_STORAGE_KEY = 'wootify_panel_token';

export function getPanelToken() {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

export function setPanelToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* storage unavailable */
  }
}

export function clearPanelToken() {
  setPanelToken('');
}

export function authHeaders(extra = {}) {
  const token = getPanelToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

export function handleUnauthorized(res) {
  if (res.status === 401) {
    clearPanelToken();
    window.dispatchEvent(new CustomEvent('wootify:unauthorized'));
  }
}

export function withBase(path) {
  if (!API_BASE) return path;
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  return `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`;
}

export async function fetchJSON(path, options = {}) {
  const headers = authHeaders(options.headers || {});
  const res = await fetch(withBase(path), { ...options, headers });
  if (!res.ok) {
    handleUnauthorized(res);
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

export async function fetchFormJSON(path, options = {}) {
  return fetchJSON(path, options);
}
