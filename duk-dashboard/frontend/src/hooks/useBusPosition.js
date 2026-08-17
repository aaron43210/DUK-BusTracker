// src/hooks/useBusPosition.js
import { usePolling } from './usePolling';
import { api }        from '../api/client';

export function useBusPosition(enabled) {
  const { data, error } = usePolling(api.latest, 2_000, enabled, []);
  return {
    position: data,
    error,
    hasFix: data?.lat != null,
  };
}
