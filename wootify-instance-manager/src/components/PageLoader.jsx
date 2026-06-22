import React from 'react';

export default function PageLoader({ label = 'Loading workspace' }) {
  return (
    <section className="card page-loader" aria-busy="true" aria-live="polite">
      <div className="page-loader__pulse" />
      <div>
        <div className="page-loader__title">{label}</div>
        <div className="muted">Preparing modules and data panels.</div>
      </div>
    </section>
  );
}