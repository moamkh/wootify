import React from 'react';

export default function EnterpriseAssetsPanel({
  selectedKey,
  busy,
  enterpriseManuals,
  enterpriseManualGroups,
  manualGroupByAssetId,
  enterpriseCatalog,
  manualDisplayName,
  setManualDisplayName,
  manualLinkUrl,
  setManualLinkUrl,
  setManualFile,
  editingManualId,
  editingManualDisplayName,
  setEditingManualDisplayName,
  editingManualLinkUrl,
  setEditingManualLinkUrl,
  editingManualGroupId,
  setEditingManualGroupId,
  catalogDisplayName,
  setCatalogDisplayName,
  catalogLinkUrl,
  setCatalogLinkUrl,
  setCatalogFile,
  editingCatalog,
  editingCatalogDisplayName,
  setEditingCatalogDisplayName,
  editingCatalogLinkUrl,
  setEditingCatalogLinkUrl,
  onUploadManual,
  onDeleteManual,
  onStartEditManual,
  onSaveEditManual,
  onCancelEditManual,
  onAddGroupFromManualEdit,
  onRenameGroupFromManualEdit,
  onDeleteGroupFromManualEdit,
  onReplaceCatalog,
  onDeleteCatalog,
  onStartEditCatalog,
  onSaveEditCatalog,
  onCancelEditCatalog,
}) {
  return (
    <section className="card section-stack">
      <div className="section-heading">
        <div>
          <p className="section-eyebrow">Content operations</p>
          <h2>Enterprise assets</h2>
          <p className="muted">Manage manuals, groups, and product catalog assets in one operational surface.</p>
        </div>
      </div>

      {!selectedKey ? <div className="muted">Save the instance before uploading manuals or the catalog.</div> : null}

      <div className="form">
        <h3>Manuals</h3>
        <div className="row">
          <label>
            Display Name
            <input value={manualDisplayName} onChange={(e) => setManualDisplayName(e.target.value)} />
          </label>
          <label>
            Link URL
            <input
              type="url"
              placeholder="https://example.com/manual"
              value={manualLinkUrl}
              onChange={(e) => setManualLinkUrl(e.target.value)}
            />
          </label>
          <label>
            PDF File
            <input type="file" accept="application/pdf,.pdf" onChange={(e) => setManualFile(e.target.files?.[0] || null)} />
          </label>
        </div>
        <button className="btn" type="button" disabled={busy || !selectedKey} onClick={onUploadManual}>
          Upload Manual
        </button>

        {enterpriseManuals.length === 0 ? (
          <div className="muted">No manuals uploaded.</div>
        ) : (
          <div className="list">
            {enterpriseManuals.map((item) => {
              const groupId = manualGroupByAssetId[item.id] || '';
              const groupName = groupId ? enterpriseManualGroups.find((g) => g.id === groupId)?.name || '-' : 'Unassigned';
              return (
                <div key={item.id} className="list-item">
                  <div className="list-main">
                    {editingManualId === item.id ? (
                      <div className="form">
                        <div className="row">
                          <label>
                            Display Name
                            <input
                              value={editingManualDisplayName}
                              onChange={(e) => setEditingManualDisplayName(e.target.value)}
                            />
                          </label>
                          <label>
                            Link URL
                            <input type="url" value={editingManualLinkUrl} onChange={(e) => setEditingManualLinkUrl(e.target.value)} />
                          </label>
                        </div>
                        <label>
                          Group
                          <select value={editingManualGroupId} onChange={(e) => setEditingManualGroupId(e.target.value)}>
                            <option value="">Unassigned</option>
                            {enterpriseManualGroups.map((group) => (
                              <option key={group.id} value={group.id}>
                                {group.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <div className="list-actions">
                          <button className="btn ghost" type="button" disabled={busy || !selectedKey} onClick={onAddGroupFromManualEdit}>
                            + Add Group
                          </button>
                        </div>
                        {enterpriseManualGroups.length > 0 ? (
                          <div className="watchlist compact-list">
                            {enterpriseManualGroups.map((group) => (
                              <div key={`group-manage-${group.id}`} className="watchlist__item compact-list__item">
                                <span>{group.name}</span>
                                <div className="list-actions">
                                  <button className="btn ghost" type="button" disabled={busy} onClick={() => onRenameGroupFromManualEdit(group)}>
                                    Rename
                                  </button>
                                  <button className="btn danger" type="button" disabled={busy} onClick={() => onDeleteGroupFromManualEdit(group)}>
                                    Delete
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : (
                      <>
                        <div className="list-title">{item.display_name || item.original_filename}</div>
                        <div className="list-meta">
                          {item.original_filename} · {(item.size_bytes / 1024).toFixed(1)} KB
                        </div>
                        <div className="list-meta">Group: {groupName}</div>
                        <div className="list-meta">
                          Link:{' '}
                          {item.link_url ? (
                            <a href={item.link_url} target="_blank" rel="noreferrer">
                              {item.link_url}
                            </a>
                          ) : (
                            '-'
                          )}
                        </div>
                      </>
                    )}
                  </div>

                  <div className="list-actions">
                    {editingManualId === item.id ? (
                      <>
                        <button className="btn" type="button" disabled={busy} onClick={() => onSaveEditManual(item.id)}>
                          Save
                        </button>
                        <button className="btn ghost" type="button" disabled={busy} onClick={onCancelEditManual}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <button className="btn" type="button" disabled={busy} onClick={() => onStartEditManual(item)}>
                        Edit
                      </button>
                    )}
                    <button className="btn danger" type="button" disabled={busy} onClick={() => onDeleteManual(item.id)}>
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <h3>Catalog</h3>
        <div className="row">
          <label>
            Display Name
            <input value={catalogDisplayName} onChange={(e) => setCatalogDisplayName(e.target.value)} />
          </label>
          <label>
            Link URL
            <input
              type="url"
              placeholder="https://example.com/catalog"
              value={catalogLinkUrl}
              onChange={(e) => setCatalogLinkUrl(e.target.value)}
            />
          </label>
          <label>
            PDF File (optional)
            <input type="file" accept="application/pdf,.pdf" onChange={(e) => setCatalogFile(e.target.files?.[0] || null)} />
          </label>
        </div>
        <div className="list-actions">
          <button className="btn" type="button" disabled={busy || !selectedKey} onClick={onReplaceCatalog}>
            Upload or Replace Catalog
          </button>
        </div>
        {enterpriseCatalog ? (
          <div className="list-item">
            <div className="list-main">
              {editingCatalog ? (
                <div className="form">
                  <div className="row">
                    <label>
                      Display Name
                      <input value={editingCatalogDisplayName} onChange={(e) => setEditingCatalogDisplayName(e.target.value)} />
                    </label>
                    <label>
                      Link URL
                      <input type="url" value={editingCatalogLinkUrl} onChange={(e) => setEditingCatalogLinkUrl(e.target.value)} />
                    </label>
                  </div>
                </div>
              ) : (
                <>
                  <div className="list-title">{enterpriseCatalog.display_name || enterpriseCatalog.original_filename}</div>
                  {enterpriseCatalog.original_filename ? (
                    <div className="list-meta">
                      {enterpriseCatalog.original_filename} · {(enterpriseCatalog.size_bytes / 1024).toFixed(1)} KB
                    </div>
                  ) : null}
                  <div className="list-meta">
                    Link:{' '}
                    {enterpriseCatalog.link_url ? (
                      <a href={enterpriseCatalog.link_url} target="_blank" rel="noreferrer">
                        {enterpriseCatalog.link_url}
                      </a>
                    ) : (
                      '-'
                    )}
                  </div>
                </>
              )}
            </div>
            <div className="list-actions">
              {editingCatalog ? (
                <>
                  <button className="btn" type="button" disabled={busy} onClick={onSaveEditCatalog}>
                    Save
                  </button>
                  <button className="btn ghost" type="button" disabled={busy} onClick={onCancelEditCatalog}>
                    Cancel
                  </button>
                </>
              ) : (
                <button className="btn" type="button" disabled={busy} onClick={onStartEditCatalog}>
                  Edit
                </button>
              )}
              <button className="btn danger" type="button" disabled={busy} onClick={onDeleteCatalog}>
                Delete
              </button>
            </div>
          </div>
        ) : (
          <div className="muted">No catalog uploaded.</div>
        )}
      </div>
    </section>
  );
}