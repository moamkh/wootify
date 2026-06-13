import React, { Suspense, lazy } from 'react';
import InstanceDetailHero from '../components/InstanceDetailHero.jsx';
import PageLoader from '../components/PageLoader.jsx';

const InstanceFormPanel = lazy(() => import('../components/InstanceFormPanel.jsx'));
const MappingExplorerPanel = lazy(() => import('../components/MappingExplorerPanel.jsx'));
const EnterpriseAssetsPanel = lazy(() => import('../components/EnterpriseAssetsPanel.jsx'));
const EnterpriseOperationsPanel = lazy(() => import('../components/EnterpriseOperationsPanel.jsx'));
const SimulationPanel = lazy(() => import('../components/SimulationPanel.jsx'));

export default function InstanceWorkspacePage({
  activeTab,
  setActiveTab,
  isEnterprisePlatform,
  heroProps,
  formProps,
  mappingProps,
  enterpriseAssetProps,
  enterpriseOperationsProps,
  simulationProps,
  conversationsCount,
  enterpriseSessionsCount,
  enterpriseManualsCount,
  onBalePvSyncContacts,
  onBalePvSyncDialogs,
}) {
  const tabs = isEnterprisePlatform
    ? [
        { id: 'config', label: 'Configuration', helper: 'Instance, routing, and content settings' },
        { id: 'assets', label: 'Assets', helper: `${enterpriseManualsCount} manuals and catalog content` },
        { id: 'operations', label: 'Operations', helper: `${enterpriseSessionsCount} enterprise sessions tracked` },
      ]
    : [
        { id: 'config', label: 'Configuration', helper: 'Instance, proxy, and Chatwoot setup' },
        { id: 'observability', label: 'Observability', helper: `${conversationsCount} conversations indexed` },
        { id: 'simulation', label: 'Simulation', helper: 'Inject synthetic platform events' },
      ];

  return (
    <div className="workspace-layout">
      <InstanceDetailHero {...heroProps} onBalePvSyncContacts={onBalePvSyncContacts} onBalePvSyncDialogs={onBalePvSyncDialogs} />

      <section className="card workspace-switcher">
        <div className="section-heading compact-heading">
          <div>
            <p className="section-eyebrow">Workspace navigation</p>
            <h2>Focused surfaces</h2>
          </div>
        </div>
        <div className="workspace-tabs" role="tablist" aria-label="Instance workspace navigation">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`workspace-tab ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <span className="workspace-tab__title">{tab.label}</span>
              <span className="workspace-tab__helper">{tab.helper}</span>
            </button>
          ))}
        </div>
      </section>

      <Suspense fallback={<PageLoader label="Loading workspace section" />}>
        {activeTab === 'config' ? <InstanceFormPanel {...formProps} /> : null}
        {activeTab === 'observability' ? <MappingExplorerPanel {...mappingProps} /> : null}
        {activeTab === 'assets' ? <EnterpriseAssetsPanel {...enterpriseAssetProps} /> : null}
        {activeTab === 'operations' ? <EnterpriseOperationsPanel {...enterpriseOperationsProps} /> : null}
        {activeTab === 'simulation' ? <SimulationPanel {...simulationProps} /> : null}
      </Suspense>
    </div>
  );
}