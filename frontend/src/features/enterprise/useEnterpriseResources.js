import { useCallback, useEffect, useState } from 'react';
import {
  getEnterpriseCatalog,
  listEnterpriseManualGroupsWithManuals,
  listEnterpriseManuals,
  listEnterpriseSessions,
} from '../../shared/api/enterprise.js';
import { PLATFORM_BALE_ENTERPRISE, PLATFORM_TELEGRAM_ENTERPRISE } from '../../app/platforms.js';

/** Loads and owns enterprise assets/session state for the selected instance. */
export function useEnterpriseResources({ selectedKey, instances }) {
  const [enterpriseManuals, setEnterpriseManuals] = useState([]);
  const [enterpriseManualGroups, setEnterpriseManualGroups] = useState([]);
  const [manualGroupByAssetId, setManualGroupByAssetId] = useState({});
  const [enterpriseCatalog, setEnterpriseCatalog] = useState(null);
  const [enterpriseSessions, setEnterpriseSessions] = useState([]);

  const reset = useCallback((includeGroups) => {
    setEnterpriseManuals([]);
    if (includeGroups) setEnterpriseManualGroups([]);
    setManualGroupByAssetId({});
    setEnterpriseCatalog(null);
    setEnterpriseSessions([]);
  }, []);

  const loadEnterpriseResources = useCallback(async (instanceKey = selectedKey) => {
    if (!instanceKey) {
      reset(false);
      return;
    }
    const row = (instances || []).find((item) => item.instance_key === instanceKey);
    const isEnterprise = row?.platform_type_key === PLATFORM_BALE_ENTERPRISE || row?.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE;
    if (!isEnterprise) {
      reset(true);
      return;
    }
    return Promise.all([
      listEnterpriseManuals(instanceKey),
      listEnterpriseManualGroupsWithManuals(instanceKey),
      getEnterpriseCatalog(instanceKey),
      listEnterpriseSessions(instanceKey),
    ]).then(([manuals, groupsPayload, catalog, sessions]) => {
      setEnterpriseManuals(manuals || []);
      setEnterpriseManualGroups(groupsPayload?.groups || []);
      setManualGroupByAssetId(groupsPayload?.manual_group_map || {});
      setEnterpriseCatalog(catalog || null);
      setEnterpriseSessions(sessions || []);
    }).catch((error) => {
      reset(true);
      throw error;
    });
  }, [selectedKey, instances, reset]);

  useEffect(() => {
    loadEnterpriseResources(selectedKey).catch(() => reset(true));
  }, [selectedKey, instances, loadEnterpriseResources, reset]);

  return {
    enterpriseManuals, setEnterpriseManuals,
    enterpriseManualGroups, setEnterpriseManualGroups,
    manualGroupByAssetId, setManualGroupByAssetId,
    enterpriseCatalog, setEnterpriseCatalog,
    enterpriseSessions, setEnterpriseSessions,
    loadEnterpriseResources,
  };
}
