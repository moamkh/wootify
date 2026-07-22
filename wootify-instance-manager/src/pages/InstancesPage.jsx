import React from 'react';

export default function InstancesPage({
  loading,
  filteredInstances,
  selectedKey,
  busy,
  healthByKey,
  maskTokenValue,
  openInstanceDetail,
  onToggleEnabled,
  onCreateInbox,
  onCreateEnterpriseInbox,
  onBalePvSyncContacts,
  onBalePvSyncDialogs,
  onBalePvRemoveChatwootContacts,
  onDelete,
  PLATFORM_TELEGRAM,
  PLATFORM_BALE_ENTERPRISE,
  PLATFORM_BALE_PV_ENTERPRISE,
  PLATFORM_TELEGRAM_ENTERPRISE,
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
            const health = healthByKey?.[item.instance_key];
            const healthLabel = !item.is_enabled
              ? 'Offline'
              : health == null
                ? 'Checking'
                : health
                  ? 'Connected'
                  : 'Unreachable';
            const healthTone = !item.is_enabled || health == null ? 'warn' : health ? 'good' : 'bad';
            const isTelegram = item.platform_type_key === PLATFORM_TELEGRAM || item.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE;
            const isBalePv = item.platform_type_key === PLATFORM_BALE_PV_ENTERPRISE;
            const isEnterprise = item.platform_type_key === PLATFORM_BALE_ENTERPRISE || item.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE;
            const botName = isTelegram
              ? item.platform_metadata?.telegram_bot_name || '-'
              : isBalePv
                ? item.platform_metadata?.bale_pv_display_name || '-'
                : item.platform_metadata?.bale_bot_name || '-';
            const botId = isTelegram
              ? item.platform_metadata?.telegram_bot_id || '-'
              : isBalePv
                ? item.platform_metadata?.bale_pv_phone_number || '-'
                : item.platform_metadata?.bale_bot_id || '-';
            const department = isTelegram
              ? item.platform_metadata?.telegram_department || '-'
              : isBalePv
                ? item.platform_metadata?.bale_pv_department || '-'
                : item.platform_metadata?.bale_department || '-';
            const maskedToken = isBalePv
              ? maskTokenValue(item.platform_metadata?.bale_pv_phone_number)
              : maskTokenValue(
                  isTelegram ? item.platform_metadata?.telegram_token : item.platform_metadata?.bale_token,
                );
            const enterpriseRoutes = item.platform_metadata?.enterprise_routes || [];

            return (
              <article
                key={item.instance_key}
                className={`instance-card ${item.is_enabled ? 'enabled' : 'disabled'} ${isSelected ? 'selected' : ''}`}
                onClick={() => openInstanceDetail(item.instance_key)}
              >
                <div className="instance-card-head">
                  <h3>{item.instance_key}</h3>
                  <div className="instance-card-pills">
                    <span className={`status-pill ${healthTone}`}>{healthLabel}</span>
                    <span className={`status-pill ${item.is_enabled ? 'good' : 'warn'}`}>{statusLabel}</span>
                  </div>
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
                  {isEnterprise ? (
                    <>
                      {item.platform_type_key === PLATFORM_BALE_ENTERPRISE ? (
                        <>
                          <button
                            className="btn"
                            disabled={busy}
                            onClick={(e) => {
                              e.stopPropagation();
                              onCreateEnterpriseInbox('customer_service', item.instance_key);
                            }}
                          >
                            Service Inbox
                          </button>
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
                        </>
                      ) : (
                        enterpriseRoutes.map((route) => (
                          <button
                            key={route.route_key}
                            className="btn"
                            disabled={busy}
                            onClick={(e) => {
                              e.stopPropagation();
                              onCreateEnterpriseInbox(route.route_key, item.instance_key);
                            }}
                          >
                            {route.display_name || route.route_key} Inbox
                          </button>
                        ))
                      )}
                    </>
                  ) : (
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        onCreateInbox(item.instance_key);
                      }}
                    >
                      {isBalePv ? 'Link Inbox' : 'Create Inbox'}
                    </button>
                  )}
                  {isBalePv && onBalePvSyncContacts ? (
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        onBalePvSyncContacts(item.instance_key);
                      }}
                    >
                      Sync Contacts
                    </button>
                  ) : null}
                  {isBalePv && onBalePvSyncDialogs ? (
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        onBalePvSyncDialogs(item.instance_key);
                      }}
                    >
                      Sync Dialogs
                    </button>
                  ) : null}
                  {isBalePv && onBalePvRemoveChatwootContacts ? (
                    <button
                      className="btn danger"
                      disabled={busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        onBalePvRemoveChatwootContacts(item.instance_key);
                      }}
                    >
                      Remove Chatwoot
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