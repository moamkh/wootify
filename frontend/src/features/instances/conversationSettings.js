import { PLATFORM_BALE_ENTERPRISE, PLATFORM_TELEGRAM_ENTERPRISE } from '../../app/platforms.js';

export function managedInboxes(form) {
  if (form.platform_type_key === PLATFORM_BALE_ENTERPRISE) {
    return [['customer_service', 'Customer Service'], ['sales', 'Sales']].map(([key, name]) => ({
      id: form[`enterprise_${key}_inbox_id`], name,
    }));
  }
  if (form.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE) {
    return (form.enterprise_routes || []).map((route) => ({ id: route.inbox_id, name: route.display_name || route.route_key }));
  }
  return [{ id: form.chatwoot_inbox_id, name: form.chatwoot_inbox_name || 'Chatwoot inbox' }];
}

export function inboxReopenEnabled(form, inboxId) {
  return form.chatwoot_reopen_conversation_by_inbox?.[String(Number(inboxId))] ?? Boolean(form.chatwoot_reopen_conversation);
}

export function setInboxReopen(form, inboxId, enabled) {
  if (!(Number(inboxId) > 0)) return { ...form, chatwoot_reopen_conversation: enabled };
  return { ...form, chatwoot_reopen_conversation_by_inbox: {
    ...form.chatwoot_reopen_conversation_by_inbox, [String(Number(inboxId))]: enabled,
  } };
}

export function isEnterprise(form) {
  return [PLATFORM_BALE_ENTERPRISE, PLATFORM_TELEGRAM_ENTERPRISE].includes(form.platform_type_key);
}
