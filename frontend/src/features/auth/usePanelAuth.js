import { useEffect, useState } from 'react';
import { getPanelToken, panelAuthStatus } from '../../shared/api/auth.js';

/** Resolves panel authentication and listens for token invalidation events. */
export function usePanelAuth() {
  const [panelAuthed, setPanelAuthed] = useState(getPanelToken() ? null : false);

  useEffect(() => {
    let cancelled = false;
    panelAuthStatus()
      .then((status) => {
        if (cancelled) return;
        setPanelAuthed(status?.auth_enabled === false ? true : Boolean(status?.authenticated));
      })
      .catch(() => {
        if (!cancelled) setPanelAuthed(Boolean(getPanelToken()));
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const onUnauthorized = () => setPanelAuthed(false);
    window.addEventListener('wootify:unauthorized', onUnauthorized);
    return () => window.removeEventListener('wootify:unauthorized', onUnauthorized);
  }, []);

  return [panelAuthed, setPanelAuthed];
}
