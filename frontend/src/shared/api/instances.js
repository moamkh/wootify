/** Instance, inbox, and conversation API. */
import { fetchJSON, withBase, authHeaders, handleUnauthorized } from './client.js';

export const listPlatformTypes = () => fetchJSON('/api/v1/platform-types');
export const listFeatures = () => fetchJSON('/api/v1/features');
export async function listInstances() {
  const data = await fetchJSON('/api/v1/instances');
  return data?.items || [];
}
export async function getInstanceHealth(instanceKey) {
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
export const createInstance = (body) => fetchJSON('/api/v1/instances', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
export const updateInstance = (instanceKey, body) => fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}`, {
  method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
export const deleteInstance = (instanceKey) => fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}`, { method: 'DELETE' });
export const createInbox = (instanceKey) => fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/chatwoot/inbox`, { method: 'POST' });
export const getChatwootWebhook = (instanceKey) => fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/chatwoot/webhook`);
export const configureChatwootWebhook = (instanceKey) => fetchJSON(
  `/api/v1/instances/${encodeURIComponent(instanceKey)}/chatwoot/webhook`,
  { method: 'POST' },
);
export const createEnterpriseRouteInbox = (instanceKey, routeKey) => fetchJSON(
  `/api/v1/instances/${encodeURIComponent(instanceKey)}/enterprise/chatwoot/inboxes/${encodeURIComponent(routeKey)}`,
  { method: 'POST' },
);
export const listConversations = async (instanceKey, q = '') => {
  const query = q ? `?q=${encodeURIComponent(q)}` : '';
  const data = await fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/conversations${query}`);
  return data?.items || [];
};
export const getConversation = (instanceKey, conversationId) => fetchJSON(
  `/api/v1/instances/${encodeURIComponent(instanceKey)}/conversations/${encodeURIComponent(conversationId)}`,
);
export const listConversationMessages = async (instanceKey, conversationId) => {
  const data = await fetchJSON(`/api/v1/instances/${encodeURIComponent(instanceKey)}/conversations/${encodeURIComponent(conversationId)}/messages`);
  return data?.items || [];
};
