import React, { useMemo } from 'react';

function routeLabel(routeKey, enterpriseRoutes) {
  const cfg = (enterpriseRoutes || []).find((r) => r.route_key === routeKey);
  return cfg?.display_name || routeKey;
}

function summarizeRoute(sessions, routeKey) {
  const rows = sessions.filter((item) => item.route_key === routeKey);
  const openCount = rows.filter((item) => item.status === 'open').length;
  const activeCount = rows.filter((item) => item.user_present).length;
  const unreadCount = rows.reduce((sum, item) => sum + Number(item.unread_count || 0), 0);
  return { rows, openCount, activeCount, unreadCount };
}

function sessionHealth(item) {
  const unread = Number(item.unread_count || 0);
  if (item.status === 'resolved' && unread > 0) return { label: 'Needs cleanup', tone: 'warn' };
  if (item.status === 'open' && unread > 0) return { label: 'Pending follow-up', tone: 'warn' };
  if (item.status === 'open' && item.user_present) return { label: 'Live', tone: 'good' };
  if (item.status === 'closed_by_user') return { label: 'User closed', tone: 'neutral' };
  if (item.status === 'resolved') return { label: 'Resolved', tone: 'good' };
  return { label: 'Idle', tone: 'neutral' };
}

export default function EnterpriseOperationsPanel({ enterpriseSessions, enterpriseRoutes }) {
  const report = useMemo(() => {
    const total = enterpriseSessions.length;
    const open = enterpriseSessions.filter((item) => item.status === 'open').length;
    const resolved = enterpriseSessions.filter((item) => item.status === 'resolved').length;
    const closed = enterpriseSessions.filter((item) => item.status === 'closed_by_user').length;
    const activeUsers = enterpriseSessions.filter((item) => item.user_present).length;
    const unreadTotal = enterpriseSessions.reduce((sum, item) => sum + Number(item.unread_count || 0), 0);
    const atRisk = enterpriseSessions.filter((item) => {
      const unread = Number(item.unread_count || 0);
      return unread > 0 || (item.status === 'resolved' && unread > 0);
    });

    const routeKeys = Array.from(new Set(enterpriseSessions.map((s) => s.route_key).filter(Boolean)));
    const dynamicRoutes = routeKeys.map((key) => ({ key, ...summarizeRoute(enterpriseSessions, key) }));

    return {
      total,
      open,
      resolved,
      closed,
      activeUsers,
      unreadTotal,
      atRisk,
      routes: dynamicRoutes,
    };
  }, [enterpriseSessions]);

  return (
    <section className="card section-stack">
      <div className="section-heading">
        <div>
          <p className="section-eyebrow">Operations</p>
          <h2>Chatwoot operations report</h2>
          <p className="muted">A more detailed session health view with route coverage, unread backlog, and operator load indicators.</p>
        </div>
      </div>

      <div className="report-grid report-grid--summary">
        <article className="metric-card">
          <span className="metric-card__label">Open sessions</span>
          <strong>{report.open}</strong>
          <span className="muted">of {report.total} tracked sessions</span>
        </article>
        <article className="metric-card">
          <span className="metric-card__label">Active customers</span>
          <strong>{report.activeUsers}</strong>
          <span className="muted">currently marked present</span>
        </article>
        <article className="metric-card">
          <span className="metric-card__label">Unread backlog</span>
          <strong>{report.unreadTotal}</strong>
          <span className="muted">messages awaiting attention</span>
        </article>
        <article className="metric-card">
          <span className="metric-card__label">Closed or resolved</span>
          <strong>{report.closed + report.resolved}</strong>
          <span className="muted">sessions outside the live queue</span>
        </article>
      </div>

      <div className="report-grid report-grid--routes">
        {report.routes.map((route) => (
          <article key={route.key} className="report-card">
            <div className="report-card__head">
              <h3>{routeLabel(route.key, enterpriseRoutes)}</h3>
              <span className={`status-pill ${route.openCount > 0 ? 'good' : 'neutral'}`}>{route.rows.length} sessions</span>
            </div>
            <div className="instance-meta report-card__meta">
              <div>
                <span className="k">Open</span>
                <span className="v">{route.openCount}</span>
              </div>
              <div>
                <span className="k">Active</span>
                <span className="v">{route.activeCount}</span>
              </div>
              <div>
                <span className="k">Unread</span>
                <span className="v">{route.unreadCount}</span>
              </div>
            </div>
          </article>
        ))}
      </div>

      <div className="report-card">
        <div className="report-card__head">
          <h3>Watchlist</h3>
          <span className={`status-pill ${report.atRisk.length ? 'warn' : 'good'}`}>
            {report.atRisk.length ? `${report.atRisk.length} needs review` : 'Healthy'}
          </span>
        </div>
        {report.atRisk.length ? (
          <div className="watchlist">
            {report.atRisk.map((item) => (
              <div key={item.id} className="watchlist__item">
                <div>
                  <div className="list-title">{routeLabel(item.route_key, enterpriseRoutes)} · Chat {item.platform_chat_id}</div>
                  <div className="list-meta">Conversation {item.chatwoot_conversation_id}{item.phone_number ? ` · ${item.phone_number}` : ''}</div>
                </div>
                <div className="watchlist__stats">
                  <span className="badge badge--warn">Unread {item.unread_count}</span>
                  <span className="badge badge--muted">{item.status}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="muted">No unread backlog or inconsistent resolved sessions detected.</div>
        )}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Route</th>
              <th>Chat ID</th>
              <th>Phone</th>
              <th>Conversation</th>
              <th>Status</th>
              <th>Health</th>
              <th>Present</th>
              <th>Unread</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {enterpriseSessions.map((item) => {
              const health = sessionHealth(item);
              return (
                <tr key={item.id}>
                  <td>{routeLabel(item.route_key, enterpriseRoutes)}</td>
                  <td>{item.platform_chat_id}</td>
                  <td>{item.phone_number || '-'}</td>
                  <td>{item.chatwoot_conversation_id}</td>
                  <td>{item.status}</td>
                  <td>
                    <span className={`badge badge--${health.tone}`}>{health.label}</span>
                  </td>
                  <td>{item.user_present ? 'yes' : 'no'}</td>
                  <td>{item.unread_count}</td>
                  <td>{new Date(item.updated_at).toLocaleString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}