import React from 'react';

export default function SimulationPanel({ simEvent, setSimEvent, onSimulate, busy }) {
  return (
    <section className="card section-stack">
      <div className="section-heading">
        <div>
          <p className="section-eyebrow">Testing</p>
          <h2>Simulate platform event</h2>
          <p className="muted">Inject a synthetic inbound event to verify routing and mapping behavior.</p>
        </div>
      </div>

      <form className="form" onSubmit={onSimulate}>
        <div className="row">
          <label>
            Instance Key
            <input
              value={simEvent.instance_key}
              onChange={(e) => setSimEvent((s) => ({ ...s, instance_key: e.target.value }))}
            />
          </label>
          <label>
            Chat ID
            <input value={simEvent.chat_id} onChange={(e) => setSimEvent((s) => ({ ...s, chat_id: e.target.value }))} />
          </label>
        </div>
        <div className="row">
          <label>
            Platform Message ID
            <input
              value={simEvent.platform_message_id}
              onChange={(e) => setSimEvent((s) => ({ ...s, platform_message_id: e.target.value }))}
            />
          </label>
          <label>
            Parent Platform Message ID
            <input
              value={simEvent.parent_platform_message_id}
              onChange={(e) => setSimEvent((s) => ({ ...s, parent_platform_message_id: e.target.value }))}
            />
          </label>
        </div>
        <label>
          Text
          <textarea value={simEvent.text} onChange={(e) => setSimEvent((s) => ({ ...s, text: e.target.value }))} rows={3} />
        </label>
        <button className="btn primary" type="submit" disabled={busy}>
          Simulate
        </button>
      </form>
    </section>
  );
}