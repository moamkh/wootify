import React from 'react';

export default function MappingExplorerPanel({
  selectedKey,
  instances,
  conversations,
  mappings,
  search,
  busy,
  selectedConversationId,
  setSelectedConversationId,
  setSearch,
  onSearchConversations,
  openInstanceDetail,
}) {
  return (
    <section className="card section-stack">
      <div className="section-heading">
        <div>
          <p className="section-eyebrow">Observability</p>
          <h2>Mapping explorer</h2>
          <p className="muted">Audit the active conversation bindings and outbound message lineage.</p>
        </div>
      </div>

      <div className="form">
        <div className="row">
          <label>
            Instance
            <select value={selectedKey} onChange={(e) => openInstanceDetail(e.target.value)}>
              <option value="">Select instance</option>
              {instances.map((item) => (
                <option key={item.instance_key} value={item.instance_key}>
                  {item.instance_key}
                </option>
              ))}
            </select>
          </label>
          <label>
            Search
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="conversation id" />
          </label>
        </div>

        <div className="list-actions">
          <button className="btn" disabled={busy || !selectedKey} onClick={onSearchConversations}>
            Filter
          </button>
        </div>

        <div className="list">
          {conversations.map((item) => (
            <div
              key={item.id}
              className={`list-item ${selectedConversationId === item.id ? 'active' : ''}`}
              onClick={() => setSelectedConversationId(item.id)}
            >
              <div className="list-main">
                <div className="list-title">platform:{item.platform_conversation_id}</div>
                <div className="list-meta">
                  chatwoot:{item.chatwoot_conversation_id} {item.is_active === false ? '(historical)' : '(current)'}
                </div>
              </div>
            </div>
          ))}
        </div>

        {selectedConversationId ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Direction</th>
                  <th>Chatwoot Msg</th>
                  <th>Platform Msg</th>
                  <th>Reply (cw/platform)</th>
                  <th>Status</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {mappings.map((item) => (
                  <tr key={item.id}>
                    <td>{item.direction}</td>
                    <td>{item.chatwoot_message_id || '-'}</td>
                    <td>{item.platform_message_id || '-'}</td>
                    <td>
                      {item.chatwoot_parent_message_id || '-'} / {item.platform_parent_message_id || '-'}
                    </td>
                    <td>{item.status}</td>
                    <td>{new Date(item.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="muted">Select a conversation to inspect message mappings.</div>
        )}
      </div>
    </section>
  );
}