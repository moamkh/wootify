/** Shared API transport, URL, and panel-token primitives. */
export const API_BASE = (import.meta.env.VITE_API_BASE || 'http://localhost:8000').replace(/\/$/, '');

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
