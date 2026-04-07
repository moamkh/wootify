/**
 * Module Overview
 * ---------------
 * Purpose: Frontend API client helpers for the Wootify manager UI.
 * Documentation Standard: module/class/public-method comments.
 */

export const API_BASE = (import.meta.env.VITE_API_BASE || 'http://localhost:8000').replace(/\/$/, '');

function withBase(path) {
  if (!API_BASE) return path;
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  return `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`;
}

async function fetchJSON(path, options = {}) {
  const res = await fetch(withBase(path), options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

async function fetchFormJSON(path, options = {}) {
  const res = await fetch(withBase(path), options);
  if (!res.ok) {
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
  body.append('file', file);
  return fetchFormJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/catalog`, {
    method: 'PUT',
    body,
  });
}

export function deleteEnterpriseCatalog(instanceKey) {
  return fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/catalog`, { method: 'DELETE' });
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
