/** Enterprise assets, sessions, inboxes, and SMS API. */
import { fetchJSON, fetchFormJSON } from './client.js';

const id = encodeURIComponent;
export async function listEnterpriseManuals(k) { const d = await fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manuals`); return d?.items || []; }
export function uploadEnterpriseManual(k, { displayName, linkUrl, file }) {
  const body = new FormData(); body.append('display_name', displayName); body.append('link_url', linkUrl); body.append('file', file);
  return fetchFormJSON(`/api/v1/instances/${id(k)}/enterprise/manuals`, { method: 'POST', body });
}
export const deleteEnterpriseManual = (k, a) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manuals/${id(a)}`, { method: 'DELETE' });
export const patchEnterpriseManual = (k, a, body) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manuals/${id(a)}`, {
  method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
export async function getEnterpriseCatalog(k) { const d = await fetchJSON(`/api/v1/instances/${id(k)}/enterprise/catalog`); return d?.item || null; }
export function replaceEnterpriseCatalog(k, { displayName, linkUrl, file }) {
  const body = new FormData(); if (displayName != null) body.append('display_name', displayName); body.append('link_url', linkUrl); if (file) body.append('file', file);
  return fetchFormJSON(`/api/v1/instances/${id(k)}/enterprise/catalog`, { method: 'PUT', body });
}
export const patchEnterpriseCatalog = (k, body) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/catalog`, {
  method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
export const deleteEnterpriseCatalog = (k) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/catalog`, { method: 'DELETE' });
export async function listEnterpriseManualGroups(k) { const d = await fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manual-groups`); return d?.items || []; }
export const createEnterpriseManualGroup = (k, name) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manual-groups`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
});
export const renameEnterpriseManualGroup = (k, g, name) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manual-groups/${id(g)}`, {
  method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
});
export const deleteEnterpriseManualGroup = (k, g) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manual-groups/${id(g)}`, { method: 'DELETE' });
export async function listEnterpriseManualGroupManuals(k, g) { const d = await fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manual-groups/${id(g)}/manuals`); return d?.items || []; }
export const listEnterpriseManualGroupsWithManuals = (k) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manual-groups-with-manuals`);
export const addManualToEnterpriseGroup = (k, g, a) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manual-groups/${id(g)}/manuals/${id(a)}`, { method: 'POST' });
export const removeManualFromEnterpriseGroup = (k, g, a) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/manual-groups/${id(g)}/manuals/${id(a)}`, { method: 'DELETE' });
export async function listEnterpriseSessions(k) { const d = await fetchJSON(`/api/v1/instances/${id(k)}/enterprise/sessions`); return d?.items || []; }
export const getEnterpriseSmsSyncConfig = (k) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/sms-sync`);
export const updateEnterpriseSmsSyncConfig = (k, body) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/sms-sync`, {
  method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
export const runEnterpriseSmsSyncNow = (k) => fetchJSON(`/api/v1/instances/${id(k)}/enterprise/sms-sync/run`, { method: 'POST' });
