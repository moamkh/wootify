/**
 * Module Overview
 * ---------------
 * Purpose: Frontend API client helpers for the Wootify manager UI.
 * Documentation Standard: module/class/public-method comments.
 */

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
    if (token) {
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    /* storage unavailable */
  }
}

export function clearPanelToken() {
  setPanelToken('');
}

function authHeaders(extra = {}) {
  const token = getPanelToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

function handleUnauthorized(res) {
  if (res.status === 401) {
    clearPanelToken();
    // Notify the app shell so it can show the login screen again.
    window.dispatchEvent(new CustomEvent('wootify:unauthorized'));
  }
}

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
  const res = await fetch(withBase('/api/v1/panel/auth/status'), {
    headers: authHeaders(),
  });
  if (!res.ok) return { authenticated: false, auth_enabled: true };
  return res.json();
}

function withBase(path) {
  if (!API_BASE) return path;
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  return `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`;
}

async function fetchJSON(path, options = {}) {
  const headers = authHeaders(options.headers || {});
  const res = await fetch(withBase(path), { ...options, headers });
  if (!res.ok) {
    handleUnauthorized(res);
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

async function fetchFormJSON(path, options = {}) {
  const headers = authHeaders(options.headers || {});
  const res = await fetch(withBase(path), { ...options, headers });
  if (!res.ok) {
    handleUnauthorized(res);
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

export function listPlatformTypes() {
  return fetchJSON('/api/v1/platform-types');
}

export function listFeatures() {
  return fetchJSON('/api/v1/features');
}

export async function listInstances() {
  const data = await fetchJSON('/api/v1/instances');
  return data?.items || [];
}

export async function getInstanceHealth(instanceKey) {
  // The health endpoint answers 503 when the connection is unhealthy, so
  // bypass fetchJSON and map the status code to a boolean instead of throwing.
  try {
    const res = await fetch(withBase(`/api/v1/instances/${encodeURIComponent(instanceKey)}/health`), {
      headers: authHeaders(),
    });
    handleUnauthorized(res);
    return res.ok;
  } catch {
    return false;
  }
}

export function createInstance(body) {
  return fetchJSON('/api/v1/instances', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function updateInstance(instanceKey, body) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function deleteInstance(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}`, { method: 'DELETE' });
}

export function createInbox(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/chatwoot/inbox`, { method: 'POST' });
}

export function createEnterpriseRouteInbox(instanceKey, routeKey) {
  return fetchJSON(
    `/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/chatwoot/inboxes/${encodeURIComponent(routeKey)}`,
    { method: 'POST' },
  );
}

export function simulatePlatformEvent(instanceKey, body) {
  return fetchJSON(`/api/v1/simulate/platform/${encodeURIComponent(instanceKey)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function listConversations(instanceKey, q = '') {
  const query = q ? `?q=${encodeURIComponent(q)}` : '';
  const data = await fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/conversations${query}`);
  return data?.items || [];
}

export function getConversation(instanceKey, conversationId) {
  return fetchJSON(
    `/api/v1/instances/${encodeURIComponent(instanceKey)}/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export async function listConversationMessages(instanceKey, conversationId) {
  const data = await fetchJSON(
    `/api/v1/instances/${encodeURIComponent(instanceKey)}/conversations/${encodeURIComponent(conversationId)}/messages`,
  );
  return data?.items || [];
}

export async function listEnterpriseManuals(instanceKey) {
  const data = await fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manuals`);
  return data?.items || [];
}

export function uploadEnterpriseManual(instanceKey, { displayName, linkUrl, file }) {
  const body = new FormData();
  body.append('display_name', displayName);
  body.append('link_url', linkUrl);
  body.append('file', file);
  return fetchFormJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manuals`, {
    method: 'POST',
    body,
  });
}

export function deleteEnterpriseManual(instanceKey, assetId) {
  return fetchJSON(
    `/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manuals/${encodeURIComponent(assetId)}`,
    { method: 'DELETE' },
  );
}

export function patchEnterpriseManual(instanceKey, assetId, body) {
  return fetchJSON(
    `/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manuals/${encodeURIComponent(assetId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

export async function getEnterpriseCatalog(instanceKey) {
  const data = await fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/catalog`);
  return data?.item || null;
}

export function replaceEnterpriseCatalog(instanceKey, { displayName, linkUrl, file }) {
  const body = new FormData();
  if (displayName != null) {
    body.append('display_name', displayName);
  }
  body.append('link_url', linkUrl);
  if (file) {
    body.append('file', file);
  }
  return fetchFormJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/catalog`, {
    method: 'PUT',
    body,
  });
}

export function patchEnterpriseCatalog(instanceKey, body) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/catalog`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function deleteEnterpriseCatalog(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/catalog`, { method: 'DELETE' });
}

export async function listEnterpriseManualGroups(instanceKey) {
  const data = await fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manual-groups`);
  return data?.items || [];
}

export function createEnterpriseManualGroup(instanceKey, groupName) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manual-groups`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: groupName }),
  });
}

export function renameEnterpriseManualGroup(instanceKey, groupId, newName) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manual-groups/${encodeURIComponent(groupId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName }),
  });
}

export function deleteEnterpriseManualGroup(instanceKey, groupId) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manual-groups/${encodeURIComponent(groupId)}`, {
    method: 'DELETE',
  });
}

export async function listEnterpriseManualGroupManuals(instanceKey, groupId) {
  const data = await fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manual-groups/${encodeURIComponent(groupId)}/manuals`);
  return data?.items || [];
}

export async function listEnterpriseManualGroupsWithManuals(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manual-groups-with-manuals`);
}

export function addManualToEnterpriseGroup(instanceKey, groupId, assetId) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manual-groups/${encodeURIComponent(groupId)}/manuals/${encodeURIComponent(assetId)}`, {
    method: 'POST',
  });
}

export function removeManualFromEnterpriseGroup(instanceKey, groupId, assetId) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/manual-groups/${encodeURIComponent(groupId)}/manuals/${encodeURIComponent(assetId)}`, {
    method: 'DELETE',
  });
}

export async function listEnterpriseSessions(instanceKey) {
  const data = await fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/sessions`);
  return data?.items || [];
}

export function getEnterpriseSmsSyncConfig(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/sms-sync`);
}

export function updateEnterpriseSmsSyncConfig(instanceKey, body) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/sms-sync`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function runEnterpriseSmsSyncNow(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/sms-sync/run`, {
    method: 'POST',
  });
}

export function getVersion() {
  return fetchJSON('/api/v1/version');
}

export function balePvSendCode(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/bale-pv/auth/send-code`, { method: 'POST' });
}

export function balePvValidateCode(instanceKey, code) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/bale-pv/auth/validate-code`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
}

export function balePvAuthStatus(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/bale-pv/auth/status`);
}

export function balePvSyncContacts(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/bale-pv/sync-contacts`, {
    method: 'POST',
  });
}


export function balePvSyncDialogs(instanceKey, loadHistory = true, historyLimit = 50) {
  const params = new URLSearchParams();
  params.set('load_history', loadHistory ? 'true' : 'false');
  params.set('history_limit', String(historyLimit));
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/bale-pv/sync-dialogs?${params.toString()}`, {
    method: 'POST',
  });
}


export function balePvRemoveChatwootContacts(instanceKey, dryRun = false) {
  const params = new URLSearchParams();
  params.set('dry_run', dryRun ? 'true' : 'false');
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/bale-pv/remove-chatwoot-contacts?${params.toString()}`, {
    method: 'POST',
  });
}


export function instagramPvCheck(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/instagram-pv/check`, {
    method: 'POST',
  });
}


export function instagramPvReconnect(instanceKey, fresh = false) {
  const params = new URLSearchParams();
  if (fresh) params.set('fresh', 'true');
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/instagram-pv/reconnect${suffix}`, {
    method: 'POST',
  });
}


export function instagramPvChallengeStart(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/instagram-pv/challenge/start`, {
    method: 'POST',
  });
}


export function instagramPvChallengeValidateCode(instanceKey, code) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/instagram-pv/challenge/validate-code`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
}


export function instagramPvChallengeResume(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/instagram-pv/challenge/resume`, {
    method: 'POST',
  });
}
