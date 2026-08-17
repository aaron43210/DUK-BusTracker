// src/hooks/useStops.js
import { usePolling } from './usePolling';
import { api }        from '../api/client';

export function useStops() {
  const stopsRes = usePolling(api.stops,         60_000, true, []);
  const geoRes   = usePolling(api.routeGeometry, 60_000, true, []);
  return {
    stops:    stopsRes.data ?? [],
    geometry: geoRes.data,
    error:    stopsRes.error || geoRes.error,
  };
}

export function useEta(enabled, targetStopId = null) {
  const { data } = usePolling(
    () => api.eta({ target_stop_id: targetStopId }),
    15_000,
    enabled,
    [targetStopId]
  );
  return data;
}
