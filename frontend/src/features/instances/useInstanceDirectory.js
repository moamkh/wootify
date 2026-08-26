import { useMemo } from 'react';

/** Derived instance lookup/filter state shared by the list and detail views. */
export function useInstanceDirectory({ instances, instanceSearch, instanceStatusFilter }) {
  const instanceMap = useMemo(() => {
    const map = {};
    for (const item of instances) map[item.instance_key] = item;
    return map;
  }, [instances]);

  const filteredInstances = useMemo(() => {
    const query = instanceSearch.trim().toLowerCase();
    return instances.filter((item) => {
      if (instanceStatusFilter === 'enabled' && !item.is_enabled) return false;
      if (instanceStatusFilter === 'disabled' && item.is_enabled) return false;
      if (!query) return true;
      const hay = [
        item.instance_key, item.platform_type_key,
        item.platform_metadata?.bale_bot_name, item.platform_metadata?.bale_department,
        item.platform_metadata?.bale_pv_display_name, item.platform_metadata?.bale_pv_department,
        item.platform_metadata?.bale_pv_phone_number, item.platform_metadata?.instagram_display_name,
        item.platform_metadata?.instagram_department, item.platform_metadata?.instagram_username,
        item.platform_metadata?.telegram_bot_name, item.platform_metadata?.telegram_department,
        item.chatwoot?.account_id, item.chatwoot?.inbox_id,
      ].map((v) => String(v ?? '').toLowerCase()).join(' ');
      return hay.includes(query);
    });
  }, [instances, instanceSearch, instanceStatusFilter]);

  return { instanceMap, filteredInstances };
}
