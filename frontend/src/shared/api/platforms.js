/** Platform simulation, Bale PV, and Instagram PV API. */
import { fetchJSON } from './client.js';
const id = encodeURIComponent;
export const simulatePlatformEvent = (k, body) => fetchJSON(`/api/v1/simulate/platform/${id(k)}`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
export const getVersion = () => fetchJSON('/api/v1/version');
export const balePvSendCode = (k) => fetchJSON(`/api/v1/instances/${id(k)}/bale-pv/auth/send-code`, { method: 'POST' });
export const balePvValidateCode = (k, code) => fetchJSON(`/api/v1/instances/${id(k)}/bale-pv/auth/validate-code`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
});
export const balePvAuthStatus = (k) => fetchJSON(`/api/v1/instances/${id(k)}/bale-pv/auth/status`);
export const balePvSyncContacts = (k) => fetchJSON(`/api/v1/instances/${id(k)}/bale-pv/sync-contacts`, { method: 'POST' });
export function balePvSyncDialogs(k, loadHistory = true, historyLimit = 50) {
  const params = new URLSearchParams(); params.set('load_history', loadHistory ? 'true' : 'false'); params.set('history_limit', String(historyLimit));
  return fetchJSON(`/api/v1/instances/${id(k)}/bale-pv/sync-dialogs?${params.toString()}`, { method: 'POST' });
}
export function balePvRemoveChatwootContacts(k, dryRun = false) {
  const params = new URLSearchParams(); params.set('dry_run', dryRun ? 'true' : 'false');
  return fetchJSON(`/api/v1/instances/${id(k)}/bale-pv/remove-chatwoot-contacts?${params.toString()}`, { method: 'POST' });
}
export const instagramPvCheck = (k) => fetchJSON(`/api/v1/instances/${id(k)}/instagram-pv/check`, { method: 'POST' });
export function instagramPvReconnect(k, fresh = false) {
  const params = new URLSearchParams(); if (fresh) params.set('fresh', 'true');
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return fetchJSON(`/api/v1/instances/${id(k)}/instagram-pv/reconnect${suffix}`, { method: 'POST' });
}
export const instagramPvChallengeStart = (k) => fetchJSON(`/api/v1/instances/${id(k)}/instagram-pv/challenge/start`, { method: 'POST' });
export const instagramPvChallengeValidateCode = (k, code) => fetchJSON(`/api/v1/instances/${id(k)}/instagram-pv/challenge/validate-code`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
});
export const instagramPvChallengeResume = (k) => fetchJSON(`/api/v1/instances/${id(k)}/instagram-pv/challenge/resume`, { method: 'POST' });
