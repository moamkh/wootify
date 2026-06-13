import React from 'react';

export default function InstanceDetailHero({
  selectedKey,
  form,
  selectedInstance,
  enabledFeatureCount,
  conversationsCount,
  mappingsCount,
  busy,
  isBalePlatform,
  isTelegramPlatform,
  isEnterpriseBalePlatform,
  isBalePvPlatform,
  isEnterpriseTelegramPlatform,
  isEnterprisePlatform,
  enterpriseRoutes,
  maskTokenValue,
  onToggleEnabled,
  onCreateInbox,
  onCreateEnterpriseInbox,
  onBalePvSyncContacts,
  onBalePvSyncDialogs,
  onDelete,
}) {
  return (
    <section className="card detail-hero workspace-hero">
      <div className="row between">
        <div>
          <p className="section-eyebrow">Instance workspace</p>
          <h2>{selectedKey || 'New Instance'}</h2>
          <p className="muted">Configuration, routing, assets, and live operational visibility.</p>
        </div>
        <span className={`status-pill ${form.is_enabled ? 'good' : 'warn'}`}>{form.is_enabled ? 'Enabled' : 'Disabled'}</span>
      </div>

      {isBalePlatform && !isBalePvPlatform ? (
        <div className="instance-token">{maskTokenValue(form.bale_token || selectedInstance?.platform_metadata?.bale_token)}</div>
      ) : null}
      {isBalePvPlatform ? (
        <div className="instance-token">{maskTokenValue(form.bale_pv_phone_number || selectedInstance?.platform_metadata?.bale_pv_phone_number)}</div>
      ) : null}
      {isTelegramPlatform ? (
        <div className="instance-token">
          {maskTokenValue(form.telegram_token || selectedInstance?.platform_metadata?.telegram_token)}
        </div>
      ) : null}

      <div className="instance-meta workspace-hero__metrics">
        {isBalePlatform && !isBalePvPlatform ? (
          <>
            <div>
              <span className="k">Bot name</span>
              <span className="v">{form.bale_bot_name || '-'}</span>
            </div>
            <div>
              <span className="k">Bot ID</span>
              <span className="v">{form.bale_bot_id || '-'}</span>
            </div>
            <div>
              <span className="k">Department</span>
              <span className="v">{form.bale_department || '-'}</span>
            </div>
          </>
        ) : null}
        {isBalePvPlatform ? (
          <>
            <div>
              <span className="k">Display name</span>
              <span className="v">{form.bale_pv_display_name || '-'}</span>
            </div>
            <div>
              <span className="k">Phone</span>
              <span className="v">{form.bale_pv_phone_number || '-'}</span>
            </div>
            <div>
              <span className="k">Department</span>
              <span className="v">{form.bale_pv_department || '-'}</span>
            </div>
          </>
        ) : null}
        {isTelegramPlatform ? (
          <>
            <div>
              <span className="k">Bot name</span>
              <span className="v">{form.telegram_bot_name || '-'}</span>
            </div>
            <div>
              <span className="k">Bot ID</span>
              <span className="v">{form.telegram_bot_id || '-'}</span>
            </div>
            <div>
              <span className="k">Department</span>
              <span className="v">{form.telegram_department || '-'}</span>
            </div>
          </>
        ) : null}
        <div>
          <span className="k">Enabled features</span>
          <span className="v">{enabledFeatureCount}</span>
        </div>
        <div>
          <span className="k">Conversations</span>
          <span className="v">{conversationsCount}</span>
        </div>
        <div>
          <span className="k">Message mappings</span>
          <span className="v">{mappingsCount}</span>
        </div>
        <div>
          <span className="k">Platform</span>
          <span className="v">{form.platform_type_key}</span>
        </div>
      </div>

      {selectedInstance ? (
        <div className="list-actions">
          <button
            className="btn"
            disabled={busy}
            onClick={() => onToggleEnabled(selectedInstance.instance_key, selectedInstance.is_enabled)}
          >
            {selectedInstance.is_enabled ? 'Disable' : 'Enable'}
          </button>
          {isEnterprisePlatform ? (
            <>
              {isEnterpriseBalePlatform ? (
                <>
                  <button className="btn" disabled={busy} onClick={() => onCreateEnterpriseInbox('customer_service')}>
                    Service Inbox
                  </button>
                  <button className="btn" disabled={busy} onClick={() => onCreateEnterpriseInbox('sales')}>
                    Sales Inbox
                  </button>
                </>
              ) : (
                (enterpriseRoutes || []).map((route) => (
                  <button key={route.route_key} className="btn" disabled={busy} onClick={() => onCreateEnterpriseInbox(route.route_key)}>
                    {route.display_name || route.route_key} Inbox
                  </button>
                ))
              )}
            </>
          ) : (
            <button className="btn" disabled={busy} onClick={() => onCreateInbox(selectedInstance.instance_key)}>
              {isBalePvPlatform ? 'Link Inbox' : 'Create Inbox'}
            </button>
          )}
          {isBalePvPlatform && onBalePvSyncContacts ? (
            <button className="btn" disabled={busy} onClick={() => onBalePvSyncContacts(selectedInstance.instance_key)}>
              Sync Contacts
            </button>
          ) : null}
          {isBalePvPlatform && onBalePvSyncDialogs ? (
            <button className="btn" disabled={busy} onClick={() => onBalePvSyncDialogs(selectedInstance.instance_key)}>
              Sync Dialogs
            </button>
          ) : null}
          <button className="btn danger" disabled={busy} onClick={() => onDelete(selectedInstance.instance_key)}>
            Delete
          </button>
        </div>
      ) : null}
    </section>
  );
}