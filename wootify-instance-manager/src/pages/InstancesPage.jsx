import React from 'react';

export default function InstancesPage({
  loading,
  filteredInstances,
  selectedKey,
  busy,
  maskTokenValue,
  openInstanceDetail,
  onToggleEnabled,
  onCreateInbox,
  onCreateEnterpriseInbox,
  onDelete,
  PLATFORM_TELEGRAM,
  PLATFORM_BALE_ENTERPRISE,
}) {
  return (
    <section className="card instance-browser section-stack">
      <div className="section-heading">
        <div>
          <p className="section-eyebrow">Workspace</p>
          <h2>Instances</h2>
          <p className="muted">A cleaner directory of active connectors, messaging identities, and inbox bindings.</p>
        </div>
      </div>

      {loading ? (
        <div className="muted">Loading...</div>
      ) : filteredInstances.length === 0 ? (
        <div className="muted">No instances found.</div>
      ) : (
        <div className="instance-card-grid">
          {filteredInstances.map((item) => {
            const isSelected = selectedKey === item.instance_key;
            const statusLabel = item.is_enabled ? 'Enabled' : 'Disabled';
            const isTelegram = item.platform_type_key === PLATFORM_TELEGRAM;
            const botName = isTelegram
              ? item.platform_metadata?.telegram_bot_name || '-'
              : item.platform_metadata?.bale_bot_name || '-';
            const botId = isTelegram
              ? item.platform_metadata?.telegram_bot_id || '-'
              : item.platform_metadata?.bale_bot_id || '-';
            const department = isTelegram
              ? item.platform_metadata?.telegram_department || '-'
              : item.platform_metadata?.bale_department || '-';
            const maskedToken = maskTokenValue(
              isTelegram ? item.platform_metadata?.telegram_token : item.platform_metadata?.bale_token,
            );

            return (
              <article
                key={item.instance_key}
                className={`instance-card ${item.is_enabled ? 'enabled' : 'disabled'} ${isSelected ? 'selected' : ''}`}
                onClick={() => openInstanceDetail(item.instance_key)}
              >
                <div className="instance-card-head">
                  <h3>{item.instance_key}</h3>
                  <span className={`status-pill ${item.is_enabled ? 'good' : 'warn'}`}>{statusLabel}</span>
                </div>

                <div className="instance-token">{maskedToken}</div>

                <div className="instance-meta">
                  <div>
                    <span className="k">Bot</span>
                    <span className="v">{botName}</span>
                  </div>
                  <div>
                    <span className="k">Bot ID</span>
                    <span className="v">{botId}</span>
                  </div>
                  <div>
                    <span className="k">Department</span>
                    <span className="v">{department}</span>
                  </div>
                  <div>
                    <span className="k">Platform</span>
                    <span className="v">{item.platform_type_key}</span>
                  </div>
                  <div>
                    <span className="k">Account</span>
                    <span className="v">{item.chatwoot?.account_id ?? '-'}</span>
                  </div>
                  <div>
                    <span className="k">Inbox</span>
                    <span className="v">{item.chatwoot?.inbox_id ?? '-'}</span>
                  </div>
                </div>

                <div className="list-actions">
                  <button
                    className="btn primary"
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      openInstanceDetail(item.instance_key);
                    }}
                  >
                    Open
                  </button>
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleEnabled(item.instance_key, item.is_enabled);
                    }}
                  >
                    {item.is_enabled ? 'Disable' : 'Enable'}
                  </button>
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (item.platform_type_key === PLATFORM_BALE_ENTERPRISE) {
                        onCreateEnterpriseInbox('customer_service', item.instance_key);
                      } else {
                        onCreateInbox(item.instance_key);
                      }
                    }}
                  >
                    {item.platform_type_key === PLATFORM_BALE_ENTERPRISE ? 'Service Inbox' : 'Create Inbox'}
                  </button>
                  {item.platform_type_key === PLATFORM_BALE_ENTERPRISE ? (
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        onCreateEnterpriseInbox('sales', item.instance_key);
                      }}
                    >
                      Sales Inbox
                    </button>
                  ) : null}
                  <button
                    className="btn danger"
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(item.instance_key);
                    }}
                  >
                    Delete
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}