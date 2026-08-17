// src/hooks/useTripState.js
import { usePolling } from './usePolling';
import { api }        from '../api/client';

export function useTripState() {
  const { data, error, online } = usePolling(api.tripState, 10_000, true, []);
  const trip = data ?? {};
  return {
    trip,
    status:             trip.status,
    tripName:           trip.trip,
    lateByMinutes:      trip.late_by_minutes     ?? null,
    nextTripTime:       trip.next_trip_time       ?? null,
    cancellationReason: trip.cancellation_reason  ?? null,
    isDemo:             trip.demo_mode === true,
    isActive:           trip.status === 'active',
    isConnecting:       trip.status === 'connecting',
    error,
    online,
  };
}
