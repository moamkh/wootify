/** Format sensitive platform credentials for display in the instance list. */
export function maskTokenValue(value) {
  const text = String(value || '').trim();
  if (!text) return '-';
  if (text.includes('***')) return text;
  if (text.length <= 6) return '*'.repeat(text.length);
  return `${'*'.repeat(Math.max(4, text.length - 6))}${text.slice(-6)}`;
}

/** Build the same save confirmation text returned by the legacy app shell. */
export function buildSaveSuccessMessage(saved) {
  const result = saved?.auto_create_inbox;
  const enterpriseResults = Array.isArray(saved?.enterprise_auto_create_inboxes)
    ? saved.enterprise_auto_create_inboxes
    : [];
  const enterpriseSummary = enterpriseResults
    .map((item) => {
      if (!item?.attempted) return null;
      if (item.inbox_id && item.created) return `${item.route_key}: created ${item.inbox_id}`;
      if (item.inbox_id) return `${item.route_key}: linked ${item.inbox_id}`;
      if (item.detail) return `${item.route_key}: ${item.detail}`;
      return `${item.route_key}: failed`;
    })
    .filter(Boolean)
    .join(', ');

  if (!result?.attempted && !enterpriseSummary) return 'Instance saved';
  if (!result?.attempted && enterpriseSummary) return `Instance saved. Enterprise inboxes: ${enterpriseSummary}.`;
  if (result.inbox_id && result.created) {
    return enterpriseSummary
      ? `Instance saved. Inbox created with ID ${result.inbox_id}. Enterprise inboxes: ${enterpriseSummary}.`
      : `Instance saved. Inbox created with ID ${result.inbox_id}.`;
  }
  if (result.inbox_id) {
    return enterpriseSummary
      ? `Instance saved. Existing inbox linked with ID ${result.inbox_id}. Enterprise inboxes: ${enterpriseSummary}.`
      : `Instance saved. Existing inbox linked with ID ${result.inbox_id}.`;
  }
  if (result.detail) {
    return enterpriseSummary
      ? `Instance saved, but auto inbox creation failed: ${result.detail}. Enterprise inboxes: ${enterpriseSummary}.`
      : `Instance saved, but auto inbox creation failed: ${result.detail}`;
  }
  return enterpriseSummary
    ? `Instance saved. Enterprise inboxes: ${enterpriseSummary}.`
    : 'Instance saved, but auto inbox creation did not complete.';
}
