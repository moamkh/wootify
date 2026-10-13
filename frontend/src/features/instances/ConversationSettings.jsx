import React from 'react';
import { managedInboxes, inboxReopenEnabled, setInboxReopen, isEnterprise } from './conversationSettings.js';

export default function ConversationSettings({ form, setForm, busy }) {
  const seen = new Set();
  const inboxes = managedInboxes(form).filter(({ id, name }) => {
    const key = Number(id) > 0 ? String(Number(id)) : name;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return (
    <div className="form-section-block">
      <h3>Conversation behavior</h3>
      <p className="muted">Change anytime and save the instance. Applies to the next incoming message; existing history is kept.</p>
      {inboxes.map(({ id, name }) => (
        <label className="checkbox" key={String(id || name)}>
          <input type="checkbox" disabled={busy || (isEnterprise(form) && !(Number(id) > 0))} checked={inboxReopenEnabled(form, id)}
            onChange={(e) => setForm((current) => setInboxReopen(current, id, e.target.checked))} />
          Reopen resolved conversation — {name}{Number(id) > 0 ? ` (inbox ${id})` : ''}
        </label>
      ))}
      {isEnterprise(form) ? <p className="muted">Link each route inbox first to enable its individual setting.</p> : null}
      <p className="muted">On: reopen the resolved conversation. Off: start a new conversation after resolution. An already open conversation is reused.</p>
    </div>
  );
}
