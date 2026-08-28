/** Panel authentication API. */
import { withBase, authHeaders, setPanelToken } from './client.js';

export { getPanelToken, setPanelToken, clearPanelToken } from './client.js';

export async function panelLogin(username, password) {
  const res = await fetch(withBase('/api/v1/panel/auth/login'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data?.detail || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  const data = await res.json();
  setPanelToken(data?.token || '');
  return data;
}

export async function panelAuthStatus() {
  const res = await fetch(withBase('/api/v1/panel/auth/status'), { headers: authHeaders() });
  if (!res.ok) return { authenticated: false, auth_enabled: true };
  return res.json();
}
