import { useCallback, useEffect, useState } from 'react';
import { listConversationMessages, listConversations } from '../../shared/api/instances.js';

/** Owns conversation search/selection and the message-to-mapping view state. */
export function useConversationExplorer({ selectedKey, search }) {
  const [conversations, setConversations] = useState([]);
  const [selectedConversationId, setSelectedConversationId] = useState('');
  const [mappings, setMappings] = useState([]);

  const loadConversations = useCallback(async (instanceKey, q = '') => {
    if (!instanceKey) {
      setConversations([]);
      setMappings([]);
      setSelectedConversationId('');
      return;
    }
    const rows = await listConversations(instanceKey, q);
    setConversations(rows);
    if (!rows.find((item) => item.id === selectedConversationId)) {
      setSelectedConversationId('');
      setMappings([]);
    }
  }, [selectedConversationId]);

  // Keep the original refresh cadence: selecting an instance loads the current search.
  useEffect(() => {
    if (!selectedKey) return;
    loadConversations(selectedKey, search);
  }, [selectedKey]);

  useEffect(() => {
    if (!selectedKey || !selectedConversationId) return;
    listConversationMessages(selectedKey, selectedConversationId)
      .then((rows) => setMappings(rows || []))
      .catch(() => setMappings([]));
  }, [selectedKey, selectedConversationId]);

  return {
    conversations,
    mappings,
    selectedConversationId,
    setSelectedConversationId,
    loadConversations,
  };
}
