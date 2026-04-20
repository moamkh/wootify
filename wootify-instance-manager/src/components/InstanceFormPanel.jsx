import React from 'react';

export default function InstanceFormPanel({
  selectedKey,
  platformTypes,
  features,
  form,
  setForm,
  busy,
  loading,
  selectedInstance,
  isBalePlatform,
  isStandardBalePlatform,
  isEnterpriseBalePlatform,
  isTelegramPlatform,
  isFeatureSupported,
  onSave,
  onNewInstance,
  onCreateEnterpriseInbox,
  onSaveEnterpriseSmsSync,
  onRunEnterpriseSmsSyncNow,
  copyTextToClipboard,
}) {
  return (
    <section className="card section-stack">
      <div className="row between">
        <div>
          <p className="section-eyebrow">Configuration</p>
          <h2>{selectedKey ? 'Edit instance' : 'Create instance'}</h2>
        </div>
        <button className="btn secondary" onClick={onNewInstance} disabled={busy}>
          New
        </button>
      </div>

      <form className="form" onSubmit={onSave}>
        <div className="row">
          <label>
            Instance Key
            <input
              value={form.instance_key}
              onChange={(e) => setForm((s) => ({ ...s, instance_key: e.target.value }))}
              disabled={Boolean(selectedKey)}
              required
            />
          </label>
          <label>
            Platform
            <select
              value={form.platform_type_key}
              onChange={(e) => setForm((s) => ({ ...s, platform_type_key: e.target.value }))}
            >
              {platformTypes.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.display_name || item.key}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={form.is_enabled}
            onChange={(e) => setForm((s) => ({ ...s, is_enabled: e.target.checked }))}
          />
          Instance enabled
        </label>

        {isBalePlatform ? (
          <>
            <h3>{isEnterpriseBalePlatform ? 'Bale enterprise metadata' : 'Bale metadata'}</h3>
            <div className="row">
              <label>
                Bale Token
                <input value={form.bale_token} onChange={(e) => setForm((s) => ({ ...s, bale_token: e.target.value }))} />
              </label>
              <label>
                Poll Interval
                <input value={form.bale_poll_interval} onChange={(e) => setForm((s) => ({ ...s, bale_poll_interval: e.target.value }))} />
              </label>
            </div>
            <label>
              Bale API Base URL
              <input value={form.bale_api_base_url} onChange={(e) => setForm((s) => ({ ...s, bale_api_base_url: e.target.value }))} />
            </label>
            <label>
              Bale File Base URL
              <input value={form.bale_file_base_url} onChange={(e) => setForm((s) => ({ ...s, bale_file_base_url: e.target.value }))} />
            </label>
            <div className="row">
              <label>
                Bot Name
                <input value={form.bale_bot_name} onChange={(e) => setForm((s) => ({ ...s, bale_bot_name: e.target.value }))} />
              </label>
              <label>
                Bot ID
                <input value={form.bale_bot_id} onChange={(e) => setForm((s) => ({ ...s, bale_bot_id: e.target.value }))} />
              </label>
            </div>
            <label>
              Department
              <input value={form.bale_department} onChange={(e) => setForm((s) => ({ ...s, bale_department: e.target.value }))} />
            </label>

            {isStandardBalePlatform ? (
              <>
                <h3>Bale phone prompt</h3>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={Boolean(form.bale_share_phone_prompt_enabled)}
                    onChange={(e) => setForm((s) => ({ ...s, bale_share_phone_prompt_enabled: e.target.checked }))}
                  />
                  Enable share-phone prompt
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={Boolean(form.bale_share_phone_prompt_only_if_missing_phone)}
                    onChange={(e) => setForm((s) => ({ ...s, bale_share_phone_prompt_only_if_missing_phone: e.target.checked }))}
                  />
                  Send prompt only if Chatwoot contact has no phone number
                </label>
                <label>
                  Share-Phone Prompt Text
                  <textarea
                    rows={3}
                    value={form.bale_share_phone_prompt_text}
                    onChange={(e) => setForm((s) => ({ ...s, bale_share_phone_prompt_text: e.target.value }))}
                  />
                </label>
              </>
            ) : null}

            {isEnterpriseBalePlatform ? (
              <>
                <h3>Enterprise messages</h3>
                {[
                  ['enterprise_welcome_text', 'Welcome Text'],
                  ['enterprise_phone_prompt_text', 'Phone Prompt Text'],
                  ['enterprise_menu_prompt_text', 'Root Menu Prompt'],
                  ['enterprise_address_prompt_text', 'Address Menu Prompt'],
                  ['enterprise_number_not_found_text', 'Number Not Found Text'],
                  ['enterprise_invalid_phone_text', 'Invalid Phone Text'],
                  ['enterprise_no_manuals_text', 'No Manuals Text'],
                  ['enterprise_no_catalog_text', 'No Catalog Text'],
                  ['enterprise_not_configured_text', 'Missing Configuration Fallback'],
                  ['enterprise_live_mode_resume_text', 'Live Session Resume Text'],
                  ['enterprise_address_tehran_alborz_text', 'Tehran and Alborz Text'],
                  ['enterprise_address_other_provinces_text', 'Other Provinces Text'],
                  ['enterprise_user_manual_link_template', 'User Manual Link Template', '{{user_manual_name}} = display name  |  {{user_manual_url}} = link URL'],
                ].map(([field, label, hint]) => (
                  <label key={field}>
                    {label}
                    {hint && <small style={{ display: 'block', marginBottom: 4, color: 'var(--text-muted, #888)', fontStyle: 'italic' }}>{hint}</small>}
                    <textarea rows={3} value={form[field]} onChange={(e) => setForm((s) => ({ ...s, [field]: e.target.value }))} />
                  </label>
                ))}
              </>
            ) : null}
          </>
        ) : null}

        {isTelegramPlatform ? (
          <>
            <h3>Telegram metadata</h3>
            <div className="row">
              <label>
                Telegram Token
                <input value={form.telegram_token} onChange={(e) => setForm((s) => ({ ...s, telegram_token: e.target.value }))} />
              </label>
              <label>
                Poll Interval
                <input value={form.telegram_poll_interval} onChange={(e) => setForm((s) => ({ ...s, telegram_poll_interval: e.target.value }))} />
              </label>
            </div>
            <label>
              Telegram API Base URL
              <input value={form.telegram_api_base_url} onChange={(e) => setForm((s) => ({ ...s, telegram_api_base_url: e.target.value }))} />
            </label>
            <label>
              Telegram File Base URL
              <input value={form.telegram_file_base_url} onChange={(e) => setForm((s) => ({ ...s, telegram_file_base_url: e.target.value }))} />
            </label>
            <div className="row">
              <label>
                Bot Name
                <input value={form.telegram_bot_name} onChange={(e) => setForm((s) => ({ ...s, telegram_bot_name: e.target.value }))} />
              </label>
              <label>
                Bot ID
                <input value={form.telegram_bot_id} onChange={(e) => setForm((s) => ({ ...s, telegram_bot_id: e.target.value }))} />
              </label>
            </div>
            <label>
              Department
              <input value={form.telegram_department} onChange={(e) => setForm((s) => ({ ...s, telegram_department: e.target.value }))} />
            </label>
            <h3>Telegram phone prompt</h3>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={Boolean(form.telegram_share_phone_prompt_enabled)}
                onChange={(e) => setForm((s) => ({ ...s, telegram_share_phone_prompt_enabled: e.target.checked }))}
              />
              Enable share-phone prompt
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={Boolean(form.telegram_share_phone_prompt_only_if_missing_phone)}
                onChange={(e) => setForm((s) => ({ ...s, telegram_share_phone_prompt_only_if_missing_phone: e.target.checked }))}
              />
              Send prompt only if Chatwoot contact has no phone number
            </label>
            <label>
              Share-Phone Prompt Text
              <textarea rows={3} value={form.telegram_share_phone_prompt_text} onChange={(e) => setForm((s) => ({ ...s, telegram_share_phone_prompt_text: e.target.value }))} />
            </label>
          </>
        ) : null}

        <h3>Proxy</h3>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={Boolean(form.proxy_enabled)}
            onChange={(e) => setForm((s) => ({ ...s, proxy_enabled: e.target.checked }))}
          />
          Enable per-instance platform proxy
        </label>
        <div className="row">
          <label>
            Protocol
            <select value={form.proxy_protocol} onChange={(e) => setForm((s) => ({ ...s, proxy_protocol: e.target.value }))} disabled={!form.proxy_enabled}>
              <option value="http">http</option>
              <option value="https">https</option>
              <option value="socks5">socks5</option>
            </select>
          </label>
          <label>
            Port
            <input value={form.proxy_port} onChange={(e) => setForm((s) => ({ ...s, proxy_port: e.target.value }))} disabled={!form.proxy_enabled} />
          </label>
        </div>
        <label>
          Host
          <input value={form.proxy_host} onChange={(e) => setForm((s) => ({ ...s, proxy_host: e.target.value }))} disabled={!form.proxy_enabled} />
        </label>
        <div className="row">
          <label>
            Username
            <input value={form.proxy_username} onChange={(e) => setForm((s) => ({ ...s, proxy_username: e.target.value }))} disabled={!form.proxy_enabled} />
          </label>
          <label>
            Password
            <input value={form.proxy_password} onChange={(e) => setForm((s) => ({ ...s, proxy_password: e.target.value }))} disabled={!form.proxy_enabled} />
          </label>
        </div>

        <h3>Chatwoot</h3>
        <label>
          Base URL
          <input value={form.chatwoot_base_url} onChange={(e) => setForm((s) => ({ ...s, chatwoot_base_url: e.target.value }))} />
        </label>
        <label>
          API Access Token
          <input value={form.chatwoot_api_access_token} onChange={(e) => setForm((s) => ({ ...s, chatwoot_api_access_token: e.target.value }))} />
        </label>
        <label>
          Account ID
          <input value={form.chatwoot_account_id} onChange={(e) => setForm((s) => ({ ...s, chatwoot_account_id: e.target.value }))} />
        </label>
        {isStandardBalePlatform ? (
          <>
            <div className="row">
              <label>
                Inbox ID
                <input value={form.chatwoot_inbox_id} onChange={(e) => setForm((s) => ({ ...s, chatwoot_inbox_id: e.target.value }))} />
              </label>
              <label>
                Inbox Name
                <input value={form.chatwoot_inbox_name} onChange={(e) => setForm((s) => ({ ...s, chatwoot_inbox_name: e.target.value }))} />
              </label>
            </div>
            <label className="checkbox">
              <input type="checkbox" checked={form.chatwoot_auto_create} onChange={(e) => setForm((s) => ({ ...s, chatwoot_auto_create: e.target.checked }))} />
              Auto create Chatwoot inbox
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={form.chatwoot_reopen_conversation} onChange={(e) => setForm((s) => ({ ...s, chatwoot_reopen_conversation: e.target.checked }))} />
              Reopen resolved Chatwoot conversation on inbound reply
            </label>
          </>
        ) : null}
        {!isEnterpriseBalePlatform ? (
          <label>
            Webhook URL
            <div className="row">
              <input value={form.chatwoot_webhook_url} readOnly placeholder="Save the instance to generate the webhook URL" />
              <button
                type="button"
                className="btn secondary"
                disabled={!form.chatwoot_webhook_url}
                onClick={async () => {
                  try {
                    await copyTextToClipboard(form.chatwoot_webhook_url);
                    alert('Webhook URL copied');
                  } catch (e) {
                    alert(e?.message || String(e));
                  }
                }}
              >
                Copy
              </button>
            </div>
          </label>
        ) : null}

        {isEnterpriseBalePlatform ? (
          <>
            {[
              ['customer_service', 'Customer Service'],
              ['sales', 'Sales'],
            ].map(([routeKey, routeLabel]) => {
              const fieldBase = routeKey === 'customer_service' ? 'enterprise_customer_service' : 'enterprise_sales';
              return (
                <div key={routeKey} className="form-section-block">
                  <h3>{routeLabel} route</h3>
                  <div className="row">
                    <label>
                      Inbox ID
                      <input
                        value={form[`${fieldBase}_inbox_id`]}
                        onChange={(e) => setForm((s) => ({ ...s, [`${fieldBase}_inbox_id`]: e.target.value }))}
                      />
                    </label>
                    <label>
                      Inbox Name
                      <input
                        value={form[`${fieldBase}_inbox_name`]}
                        onChange={(e) => setForm((s) => ({ ...s, [`${fieldBase}_inbox_name`]: e.target.value }))}
                      />
                    </label>
                  </div>
                  <label>
                    {routeLabel} Webhook URL
                    <div className="row">
                      <input value={form[`${fieldBase}_webhook_url`]} readOnly placeholder={`Save the instance to generate the ${routeLabel.toLowerCase()} webhook URL`} />
                      <button
                        type="button"
                        className="btn secondary"
                        disabled={!form[`${fieldBase}_webhook_url`]}
                        onClick={async () => {
                          try {
                            await copyTextToClipboard(form[`${fieldBase}_webhook_url`]);
                            alert(`${routeLabel} webhook URL copied`);
                          } catch (e) {
                            alert(e?.message || String(e));
                          }
                        }}
                      >
                        Copy
                      </button>
                    </div>
                  </label>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={Boolean(form[`${fieldBase}_auto_create`] )}
                      onChange={(e) => setForm((s) => ({ ...s, [`${fieldBase}_auto_create`]: e.target.checked }))}
                    />
                    Auto create {routeLabel.toLowerCase()} inbox
                  </label>
                  <label>
                    Waiting Text
                    <textarea rows={3} value={form[`${fieldBase}_waiting_text`]} onChange={(e) => setForm((s) => ({ ...s, [`${fieldBase}_waiting_text`]: e.target.value }))} />
                  </label>
                  <label>
                    Accepted Text
                    <textarea rows={3} value={form[`${fieldBase}_accepted_text`]} onChange={(e) => setForm((s) => ({ ...s, [`${fieldBase}_accepted_text`]: e.target.value }))} />
                  </label>
                  <label>
                    Unread Notification Text
                    <textarea rows={3} value={form[`${fieldBase}_unread_text`]} onChange={(e) => setForm((s) => ({ ...s, [`${fieldBase}_unread_text`]: e.target.value }))} />
                  </label>
                  <button type="button" className="btn" disabled={busy || !selectedKey} onClick={() => onCreateEnterpriseInbox(routeKey)}>
                    Create or Link {routeLabel} Inbox
                  </button>
                </div>
              );
            })}

            <h3>External SMS Sync (Novin)</h3>
            <label className="checkbox">
              <input type="checkbox" checked={Boolean(form.enterprise_sms_sync_enabled)} onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_sync_enabled: e.target.checked }))} />
              Enable SMS sync to Bale users by shared phone number
            </label>
            <label>
              API URL
              <input value={form.enterprise_sms_api_url} onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_api_url: e.target.value }))} />
            </label>
            <label>
              API Token
              <input value={form.enterprise_sms_api_token} onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_api_token: e.target.value }))} />
            </label>
            <div className="row">
              <label>
                Token Header
                <input value={form.enterprise_sms_token_header} onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_token_header: e.target.value }))} />
              </label>
              <label>
                Token Prefix
                <input value={form.enterprise_sms_token_prefix} onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_token_prefix: e.target.value }))} />
              </label>
            </div>
            <div className="row">
              <label>
                Poll Interval (minutes)
                <input value={form.enterprise_sms_poll_interval_minutes} onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_poll_interval_minutes: e.target.value }))} />
              </label>
              <label>
                HTTP Timeout (seconds)
                <input value={form.enterprise_sms_http_timeout_seconds} onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_http_timeout_seconds: e.target.value }))} />
              </label>
            </div>
            <label>
              LastId Cursor
              <input value={form.enterprise_sms_last_id} onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_last_id: e.target.value }))} />
            </label>
            <div className="row">
              <button type="button" className="btn" disabled={busy || !selectedKey} onClick={onSaveEnterpriseSmsSync}>
                Save SMS Sync Config
              </button>
              <button type="button" className="btn secondary" disabled={busy || !selectedKey} onClick={onRunEnterpriseSmsSyncNow}>
                Run SMS Sync Now
              </button>
            </div>
          </>
        ) : null}

        <h3>Features</h3>
        {features.map((feature) => {
          const supported = isFeatureSupported(feature.key);
          const runtimeOverride = (selectedInstance?.feature_overrides || []).find((item) => item.feature_key === feature.key);
          const reason = !supported ? 'unsupported for selected platform' : runtimeOverride?.disabled_reason || '';
          return (
            <label className="checkbox" key={feature.key} title={feature.description}>
              <input
                type="checkbox"
                checked={Boolean(form.feature_overrides[feature.key])}
                disabled={!supported}
                onChange={(e) => setForm((s) => ({ ...s, feature_overrides: { ...s.feature_overrides, [feature.key]: e.target.checked } }))}
              />
              {feature.display_name}
              {reason ? ` (${reason})` : ''}
            </label>
          );
        })}

        <button className="btn primary" type="submit" disabled={busy || loading}>
          {selectedKey ? 'Update Instance' : 'Create Instance'}
        </button>
      </form>
    </section>
  );
}