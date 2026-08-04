/**
 * RouteViewScreen.tsx
 * Home screen — shows:
 *  - "Route View" heading with LIVE badge
 *  - Trip pill (Trip 1 → DESTINATION)
 *  - Stats row: Next stop ETA | ETA to destination | Stops done
 *  - Stop timeline (visited + upcoming)
 *  - Live Map thumbnail that opens MapFullScreen on tap
 */

import React, { useEffect, useState, useRef, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  StatusBar,
  ActivityIndicator,
  RefreshControl,
  TouchableOpacity,
  Image,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Ionicons from '@react-native-vector-icons/ionicons';
import {
  Map,
  Camera,
  GeoJSONSource,
  Layer,
  Marker,
} from '@maplibre/maplibre-react-native';
import Colors from '../theme/colors';
import TopBar from '../components/TopBar';
import { trackingApi } from '../services/api';
import { getUser } from '../services/storage';

const MAP_STYLE    = 'https://tiles.openfreemap.org/styles/liberty';
// Geographic center of Thiruvananthapuram city — used as fallback before any data loads
const DEFAULT_CENTER: [number, number] = [76.9366, 8.5241];
const DEFAULT_ZOOM   = 12;
const POLL_MS        = 2500;
// Height per timeline row × 6 visible rows + card padding
const TIMELINE_VISIBLE_HEIGHT = 56 * 6 + 40;

function todayStr() { return new Date().toISOString().split('T')[0]; }

function formatLastUpdated(isoString: string) {
  const d = new Date(isoString);
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + ', ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// ── Daily Scheduled Timetables ───────────────────────────────────────────────
const MORNING_SCHEDULE: Record<string, string> = {
  'Central Polytechnic':          '07:30 AM',
  'Vattiyoorkavu Jn':             '07:35 AM',
  'Manjadimoodu':                 '07:38 AM',
  'Maruthankuzhi':                '07:42 AM',
  'Sasthamangalam':               '07:47 AM',
  'Vellayambalam':                '07:52 AM',
  'Thampanoor':                   '08:00 AM',
  'Chandrasekharan Nair Stadium': '08:08 AM',
  'PMG':                          '08:12 AM',
  'Pattom':                       '08:16 AM',
  'Kesavadasapuram':              '08:22 AM',
  'Ulloor':                       '08:27 AM',
  'Pongumoodu':                   '08:32 AM',
  'Sreekaryam':                   '08:37 AM',
  'Chavadimukku':                 '08:42 AM',
  'Karyavattom':                  '08:48 AM',
  'IIITMK':                       '08:52 AM',
  'Technopark Front':             '08:56 AM',
  'Kazhakuttam':                  '09:02 AM',
  'Pallipuram':                   '09:12 AM',
  'Digital University Kerala':    '09:20 AM',
};

const EVENING_SCHEDULE: Record<string, string> = {
  'Digital University Kerala':    '05:40 PM',
  'Pallipuram':                   '05:48 PM',
  'Kazhakuttam':                  '05:58 PM',
  'Technopark Front':             '06:04 PM',
  'IIITMK':                       '06:08 PM',
  'Karyavattom':                  '06:12 PM',
  'Chavadimukku':                 '06:18 PM',
  'Sreekaryam':                   '06:23 PM',
  'Pongumoodu':                   '06:28 PM',
  'Ulloor':                       '06:33 PM',
  'Kesavadasapuram':              '06:38 PM',
  'Pattom':                       '06:44 PM',
  'PMG':                          '06:48 PM',
  'Chandrasekharan Nair Stadium': '06:52 PM',
  'Thampanoor':                   '07:00 PM',
  'Vellayambalam':                '07:08 PM',
  'Sasthamangalam':               '07:13 PM',
  'Maruthankuzhi':                '07:18 PM',
  'Manjadimoodu':                 '07:22 PM',
  'Vattiyoorkavu Jn':             '07:25 PM',
  'Central Polytechnic':          '07:30 PM',
};

function parseTimeToMinutes(timeStr: string): number | null {
  if (!timeStr) return null;
  const match = timeStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!match) return null;
  let hours = parseInt(match[1], 10);
  const minutes = parseInt(match[2], 10);
  const period = match[3].toUpperCase();
  if (period === 'PM' && hours !== 12) hours += 12;
  if (period === 'AM' && hours === 12) hours = 0;
  return hours * 60 + minutes;
}

function formatMinutesToTime(totalMins: number): string {
  let m = ((totalMins % 1440) + 1440) % 1440;
  let hours = Math.floor(m / 60);
  const mins = m % 60;
  const period = hours >= 12 ? 'PM' : 'AM';
  if (hours > 12) hours -= 12;
  if (hours === 0) hours = 12;
  const hStr = hours < 10 ? `0${hours}` : `${hours}`;
  const mStr = mins < 10 ? `0${mins}` : `${mins}`;
  return `${hStr}:${mStr} ${period}`;
}

function computeEstimatedTime(scheduledStr: string, delayMinutes: number): string | null {
  const schedMins = parseTimeToMinutes(scheduledStr);
  if (schedMins == null) return null;
  return formatMinutesToTime(schedMins + delayMinutes);
}

function getDelayBadge(actualStr?: string | null, scheduledStr?: string | null, globalLateMins?: number | null) {
  if (actualStr && scheduledStr) {
    const actMins = parseTimeToMinutes(actualStr);
    const schedMins = parseTimeToMinutes(scheduledStr);
    if (actMins != null && schedMins != null) {
      const diff = actMins - schedMins;
      if (diff > 1) return { text: `+${diff}m delay`, type: 'late' as const, diff };
      if (diff < -1) return { text: `${Math.abs(diff)}m ahead`, type: 'ahead' as const, diff };
      return { text: 'On time', type: 'ahead' as const, diff: 0 };
    }
  }
  if (globalLateMins != null) {
    if (globalLateMins > 0) return { text: `+${globalLateMins}m delay`, type: 'late' as const, diff: globalLateMins };
    if (globalLateMins < 0) return { text: `${Math.abs(globalLateMins)}m ahead`, type: 'ahead' as const, diff: globalLateMins };
    return { text: 'On time', type: 'ahead' as const, diff: 0 };
  }
  return null;
}

/**
 * Compute the map center + zoom for the mini-map thumbnail.
 *
 * Priority order:
 *  1. Bus live position (center on it tightly — zoom 13 so city area is clear)
 *  2. Computed center of all stop coordinates with auto-zoom to fit the route
 *  3. Hard-coded Thiruvananthapuram default
 *
 * Uses center+zoom (not `bounds`) so it works identically on iOS and Android.
 */
function getMapViewport(
  stops: any[],
  busCoord: [number, number] | null,
): { center: [number, number]; zoom: number } {
  // 1. Bus is live — center on it, zoomed in enough to show the city
  if (busCoord) {
    return { center: busCoord, zoom: 13 };
  }

  // 2. Stops loaded — compute bounding-box center + an appropriate zoom
  if (stops.length) {
    const lons = stops.map(s => Number(s.lon));
    const lats = stops.map(s => Number(s.lat));
    const centerLon = (Math.min(...lons) + Math.max(...lons)) / 2;
    const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
    const span = Math.max(
      Math.max(...lons) - Math.min(...lons),
      Math.max(...lats) - Math.min(...lats),
    );
    let zoom = DEFAULT_ZOOM;
    if      (span > 1.0) zoom = 9;
    else if (span > 0.5) zoom = 10;
    else if (span > 0.2) zoom = 11;
    else if (span > 0.1) zoom = 12;
    else                 zoom = 13;
    return { center: [centerLon, centerLat], zoom };
  }

  // 3. Nothing yet — show Thiruvananthapuram
  return { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM };
}

export default function RouteViewScreen({ route, navigation }: any) {
  const { boardingPoint } = route?.params ?? {};

  const [user,         setUser]         = useState<any>(null);
  const [tripState,    setTripState]    = useState<any>(null);
  const [busPosition,  setBusPosition]  = useState<any>(null);
  const [routeHistory, setRouteHistory] = useState<any>(null);
  const [eta,          setEta]          = useState<any>(null);
  const [plannedCoords, setPlannedCoords] = useState<[number, number][]>([]);
  const [trailCoords,   setTrailCoords]   = useState<[number, number][]>([]);
  const [stops,         setStops]         = useState<any[]>([]);
  const [loading,       setLoading]       = useState(true);
  const [refreshing,    setRefreshing]    = useState(false);
  const [error,         setError]         = useState('');

  const [animatedBusCoord, setAnimatedBusCoord] = useState<[number, number] | null>(null);
  const [busBearing, setBusBearing] = useState<number>(0);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const animIntervalRef = useRef<any>(null);
  const currentPosRef = useRef<[number, number] | null>(null);
  const cameraRef   = useRef<any>(null);

  function calculateBearing(lon1: number, lat1: number, lon2: number, lat2: number): number {
    const dLon = ((lon2 - lon1) * Math.PI) / 180;
    const y = Math.sin(dLon) * Math.cos((lat2 * Math.PI) / 180);
    const x =
      Math.cos((lat1 * Math.PI) / 180) * Math.sin((lat2 * Math.PI) / 180) -
      Math.sin((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.cos(dLon);
    const brng = (Math.atan2(y, x) * 180) / Math.PI;
    return (brng + 360) % 360;
  }

  function haversineDistKm(lon1: number, lat1: number, lon2: number, lat2: number) {
    const R = 6371;
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLon = ((lon2 - lon1) * Math.PI) / 180;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  const animateBusTo = async (targetLat: number, targetLon: number) => {
    const startPos = currentPosRef.current || [targetLon, targetLat];
    const [startLon, startLat] = startPos;

    const distKm = haversineDistKm(startLon, startLat, targetLon, targetLat);
    if (!currentPosRef.current || distKm > 8.0) {
      setAnimatedBusCoord([targetLon, targetLat]);
      currentPosRef.current = [targetLon, targetLat];
      return;
    }

    if (distKm < 0.0005) {
      setAnimatedBusCoord([targetLon, targetLat]);
      currentPosRef.current = [targetLon, targetLat];
      return;
    }

    let polyline = [[startLon, startLat], [targetLon, targetLat]];
    try {
      const seg = await trackingApi.getRouteSegment(startLat, startLon, targetLat, targetLon);
      if (seg?.data?.coordinates?.length >= 2) {
        polyline = seg.data.coordinates;
      }
    } catch (_) {}

    const cumDists = [0];
    for (let i = 1; i < polyline.length; i++) {
      const d = haversineDistKm(polyline[i-1][0], polyline[i-1][1], polyline[i][0], polyline[i][1]);
      cumDists.push(cumDists[i-1] + d);
    }
    const totalDist = cumDists[cumDists.length - 1];

    if (totalDist <= 0.00001) {
      setAnimatedBusCoord([targetLon, targetLat]);
      currentPosRef.current = [targetLon, targetLat];
      return;
    }

    if (animIntervalRef.current) clearInterval(animIntervalRef.current);
    
    const animDurationMs = 2500;
    const startTime = Date.now();
    const fps = 25; // 25 updates per second

    if (cameraRef.current?.easeTo) {
      cameraRef.current.easeTo({
        center: [targetLon, targetLat],
        duration: animDurationMs,
      });
    }

    animIntervalRef.current = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const progress = Math.min(1.0, elapsed / animDurationMs);
      
      const currentDist = progress * totalDist;
      let segIdx = 0;
      while (segIdx < cumDists.length - 2 && cumDists[segIdx + 1] < currentDist) {
        segIdx++;
      }

      const p1 = polyline[segIdx];
      const p2 = polyline[segIdx + 1];
      const segSpan = (cumDists[segIdx + 1] - cumDists[segIdx]) || 0.00001;
      const segFrac = Math.max(0, Math.min(1, (currentDist - cumDists[segIdx]) / segSpan));

      const curLon = p1[0] + (p2[0] - p1[0]) * segFrac;
      const curLat = p1[1] + (p2[1] - p1[1]) * segFrac;
      const bearing = calculateBearing(p1[0], p1[1], p2[0], p2[1]);

      currentPosRef.current = [curLon, curLat];
      setAnimatedBusCoord([curLon, curLat]);
      setBusBearing(bearing);

      if (progress >= 1.0) {
        clearInterval(animIntervalRef.current);
        animIntervalRef.current = null;
        const finalPoint = polyline[polyline.length - 1] as [number, number];
        currentPosRef.current = finalPoint;
        setAnimatedBusCoord(finalPoint);
      }
    }, 1000 / fps);
  };

  useEffect(() => { getUser().then(u => { if (u) setUser(u); }); }, []);

  const fetchData = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    setError('');
    try {
      const [tripRes, busRes, historyRes, stopsRes, geomRes] = await Promise.all([
        trackingApi.getTripState(),
        trackingApi.getLatest().catch(() => null),
        trackingApi.getRouteHistory().catch(() => null),
        trackingApi.getStops().catch(() => null),
        trackingApi.getRouteGeometry().catch(() => null),
      ]);
      setTripState(tripRes.data);

      const tripData = tripRes.data;
      const isSched = Boolean(
        (tripData?.trip === 'morning' || tripData?.trip === 'evening' || tripData?.trip_id != null) &&
        tripData?.trip !== 'unscheduled' &&
        tripData?.trip !== 'idle' &&
        tripData?.trip !== 'offline' &&
        tripData?.status !== 'completed'
      );

      if (busRes) {
        setBusPosition(busRes.data);
        const targetLon = Number(busRes.data.lon);
        const targetLat = Number(busRes.data.lat);
        if (targetLon && targetLat) {
          animateBusTo(targetLat, targetLon);
        }
      }
      if (historyRes) setRouteHistory(historyRes.data);
      if (stopsRes)   setStops(stopsRes.data ?? []);
      if (geomRes?.data?.coordinates?.length) {
        setPlannedCoords(geomRes.data.coordinates);
      }

      if (isSched && tripData?.trip_id) {
        try {
          const traceRes = await trackingApi.getTripTrace(tripData.trip_id);
          if (traceRes?.data?.length) {
            setTrailCoords(traceRes.data);
          }
        } catch (_) {}
      } else if (!isSched) {
        setTrailCoords([]);
      }

      const stopId = user?.boarding_stop_id ?? boardingPoint?.id;
      if (stopId) {
        const etaRes = await trackingApi.getEta(stopId).catch(() => null);
        if (etaRes) setEta(etaRes.data);
      }
      if (busRes?.data?.lat && cameraRef.current) {
        cameraRef.current.flyTo({
          center:   [busRes.data.lon, busRes.data.lat] as [number, number],
          zoom:     13,
          duration: 800,
        });
      }
    } catch {
      setError('Could not load bus data. Pull down to refresh.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [user, boardingPoint]);

  useEffect(() => {
    fetchData();
    intervalRef.current = setInterval(() => fetchData(), POLL_MS);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [fetchData]);

  // Derived values
  const isLive    = busPosition?.is_live === true;
  const rawTrip   = tripState?.trip ?? '—';
  const tripName  = rawTrip.toLowerCase() === 'forward' ? 'Morning' : (rawTrip.toLowerCase() === 'reverse' ? 'Evening' : (rawTrip.charAt(0).toUpperCase() + rawTrip.slice(1)));
  const status    = tripState?.status ?? 'loading';
  const isOnline  = status !== 'offline' && status !== 'loading';

  const isScheduled = Boolean(
    (tripName.toLowerCase() === 'morning' || tripName.toLowerCase() === 'evening' || tripName.toLowerCase() === 'forward' || tripName.toLowerCase() === 'reverse' || tripState?.trip_id != null) &&
    tripName.toLowerCase() !== 'unscheduled' &&
    tripName.toLowerCase() !== 'idle' &&
    tripName.toLowerCase() !== 'offline' &&
    status !== 'completed'
  );
  const destination = tripState?.destination ?? 'DUK CAMPUS';

  const busCoord: [number, number] | null =
    busPosition?.lat ? [busPosition.lon, busPosition.lat] : null;

  const trailGeoJSON = {
    type: 'Feature' as const,
    geometry: { type: 'LineString' as const, coordinates: trailCoords },
    properties: {},
  };

  // Build full stop timeline merging visited times with upcoming stops
  const visitedMap: Record<string, string> = routeHistory?.arrivalTimes ?? {};
  const visitedNames = new Set(Object.keys(routeHistory?.visitedStops ?? {}));
  const currentStopName = [...visitedNames].pop();

  const etaMinutes  = eta?.eta_minutes != null ? Math.round(eta.eta_minutes) : null;
  const doneCount   = visitedNames.size;
  const totalCount  = stops.length;

  // Full timeline: all stops with daily scheduled time, live ETA, visit status, and delay calculation
  const isEvening = tripState?.trip?.toLowerCase().includes('evening') || tripState?.trip === 'reverse' || new Date().getHours() >= 14;
  const globalDelay = tripState?.late_by_minutes ?? 0;

  const timeline = stops.map((stop: any) => {
    const isVisited = visitedNames.has(stop.name);
    const isCurrent = stop.name === currentStopName;
    const actualArrival = visitedMap[stop.name] ?? null;
    const scheduledDaily = stop.scheduled_time ?? (isEvening ? EVENING_SCHEDULE[stop.name] : MORNING_SCHEDULE[stop.name]) ?? (isEvening ? '06:00 PM' : '08:00 AM');

    let liveTime: string | null = null;
    let delayType: 'late' | 'ahead' | 'ontime' | 'none' = 'none';
    let statusSubtext = '';

    if (isVisited) {
      liveTime = actualArrival ?? scheduledDaily;
      statusSubtext = 'Arrived';
      const delay = getDelayBadge(actualArrival, scheduledDaily, null);
      if (delay) {
        delayType = delay.type === 'late' ? 'late' : 'ahead';
      }
    } else if (isOnline) {
      // If live trip is active, compute estimated arrival time based on delay
      liveTime = computeEstimatedTime(scheduledDaily, globalDelay);
      if (globalDelay > 1) {
        delayType = 'late';
      } else if (globalDelay < -1) {
        delayType = 'ahead';
      } else {
        delayType = 'ontime';
      }

      if (isCurrent) {
        statusSubtext = etaMinutes != null ? `Approaching • ETA ${etaMinutes} min` : 'Next Stop';
      } else if (delayType === 'late') {
        statusSubtext = `+${globalDelay}m delay`;
      } else if (delayType === 'ahead') {
        statusSubtext = `${Math.abs(globalDelay)}m ahead`;
      } else {
        statusSubtext = 'On time';
      }
    } else {
      statusSubtext = 'Scheduled';
    }

    return {
      id: stop.id,
      name: stop.name,
      scheduledTime: scheduledDaily,
      liveTime,
      delayType,
      statusSubtext,
      isVisited,
      isCurrent,
    };
  });

  if (loading) {
    return (
      <SafeAreaView style={S.safe} edges={['top']}>
        <TopBar />
        <View style={S.centeredFill}>
          <ActivityIndicator size="large" color={Colors.mint} />
          <Text style={S.loadingText}>Getting live bus data…</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={S.safe} edges={['top']}>
      <StatusBar barStyle="dark-content" backgroundColor={Colors.bgGray} />
      <TopBar />

      <ScrollView
        contentContainerStyle={S.scrollContent}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => fetchData(true)}
            tintColor={Colors.mint}
          />
        }
      >
        {/* ── Header ───────────────────────────────────────────────────── */}
        <View style={S.headerRow}>
          <Text style={S.screenTitle}>Route View</Text>
          <View style={[S.liveBadge, !isOnline && S.offlineBadge]}>
            <View style={[S.liveDot, !isOnline && S.offlineDot]} />
            <Text style={[S.liveText, !isOnline && S.offlineText]}>
              {isOnline ? 'LIVE' : 'OFFLINE'}
            </Text>
          </View>
        </View>

        {/* ── Error banner ─────────────────────────────────────────────── */}
        {error ? (
          <View style={S.errorBanner}>
            <Ionicons name="alert-circle-outline" size={16} color={Colors.danger} />
            <Text style={S.errorBannerText}>{error}</Text>
          </View>
        ) : null}

        {/* ── Trip pill row ────────────────────────────────────────────── */}
        <View style={S.tripRow}>
          <View style={[S.tripPill, !isOnline && S.tripPillOffline]}>
            <Text style={[S.tripPillText, !isOnline && S.tripPillTextOff]}>
              {isOnline ? tripName : 'Not in Service'}
            </Text>
          </View>
          {isOnline && (
            <>
              <Ionicons name="arrow-forward" size={18} color={Colors.darkGray} style={S.tripArrow} />
              <Text style={S.tripDest} numberOfLines={1}>{destination.toUpperCase()}</Text>
            </>
          )}
        </View>

        {/* ── Stats row ────────────────────────────────────────────────── */}
        <View style={S.statsCard}>
          <View style={S.statItem}>
            <Text style={S.statValue}>
              {etaMinutes != null ? `${etaMinutes} min` : '—'}
            </Text>
            <Text style={S.statLabel}>NEXT STOP</Text>
          </View>
          <View style={S.statDivider} />
          <View style={S.statItem}>
            <Text style={S.statValue}>
              {busPosition?.speed != null ? `${Math.round(busPosition.speed)} km/h` : '—'}
            </Text>
            <Text style={S.statLabel}>SPEED</Text>
          </View>
          <View style={S.statDivider} />
          <View style={S.statItem}>
            <Text style={S.statValue}>{`${doneCount}/${totalCount}`}</Text>
            <Text style={S.statLabel}>STOPS</Text>
          </View>
        </View>

        {/* ── Stop timeline card — 6 rows visible, rest scrollable ────── */}
        <View style={S.card}>
          {timeline.length === 0 ? (
            <View style={S.emptyState}>
              <Ionicons name="bus-outline" size={28} color={Colors.lightGray} />
              <Text style={S.emptyStateText}>
                {isOnline ? 'No stop arrivals recorded yet.' : 'Bus is not currently in service.'}
              </Text>
            </View>
          ) : (
            <ScrollView
              style={{ maxHeight: TIMELINE_VISIBLE_HEIGHT }}
              showsVerticalScrollIndicator={true}
              nestedScrollEnabled={true}
              indicatorStyle="black"
            >
              {timeline.map((item, idx) => {
                const isLast = idx === timeline.length - 1;
                return (
                  <View key={`${item.id}-${idx}`} style={S.timelineRow}>
                    {/* Left: Scheduled Time (Top) & Live/ETA Time (Bottom in Red/Green) */}
                    <View style={S.timeLeftCol}>
                      <Text style={S.schedTimeText}>{item.scheduledTime}</Text>
                      {item.liveTime ? (
                        <Text style={[
                          S.liveTimeText,
                          item.delayType === 'late' && S.timeTextLate,
                          (item.delayType === 'ahead' || item.delayType === 'ontime') && S.timeTextAhead,
                        ]}>
                          {item.liveTime}
                        </Text>
                      ) : (
                        <Text style={S.liveTimeTextMuted}>—</Text>
                      )}
                    </View>

                    {/* Center: Track line & Dot / Bus Badge */}
                    <View style={S.timelineTrackCol}>
                      {item.isCurrent ? (
                        <View style={S.busBadgeWrapper}>
                          <View style={S.busBadgeCircle}>
                            <Ionicons name="bus" size={13} color="#ffffff" />
                          </View>
                        </View>
                      ) : (
                        <View style={[
                          S.trackDot,
                          item.isVisited && S.trackDotVisited,
                        ]} />
                      )}
                      {!isLast && (
                        <View style={[
                          S.trackLine,
                          item.isVisited && S.trackLineVisited,
                        ]} />
                      )}
                    </View>

                    {/* Right: Stop Name & Subtext */}
                    <View style={S.stopInfoCol}>
                      <Text style={[
                        S.stopName,
                        item.isCurrent && S.stopNameCurrent,
                        item.isVisited && S.stopNameVisited,
                      ]} numberOfLines={1}>
                        {item.name}
                      </Text>
                      {item.statusSubtext ? (
                        <Text style={[
                          S.stopSubtext,
                          item.isCurrent && S.stopSubtextCurrent,
                          item.delayType === 'late' && !item.isVisited && S.stopSubtextLate,
                          (item.delayType === 'ahead' || item.delayType === 'ontime') && !item.isVisited && S.stopSubtextAhead,
                        ]}>
                          {item.statusSubtext}
                        </Text>
                      ) : null}
                    </View>
                  </View>
                );
              })}
            </ScrollView>
          )}
        </View>

        {/* ── Live Map thumbnail ───────────────────────────────────────── */}
        <View style={S.sectionHeaderRow}>
          <Text style={S.sectionTitle}>Live Map</Text>
          {busPosition?.server_time && (
            <Text style={S.lastUpdatedText}>
              Last updated: {formatLastUpdated(busPosition.server_time)}
            </Text>
          )}
        </View>
        <TouchableOpacity
          style={S.mapCard}
          activeOpacity={0.92}
          onPress={() => navigation.navigate('MapFull')}
        >
          {/* Actual mini-map — starts at Thiruvananthapuram; Camera flies to bus when data loads */}
          <Map
            style={S.miniMap}
            mapStyle={MAP_STYLE}
            attributionPosition={{ bottom: 8, right: 8 }}
          >
            {(() => {
              const { center, zoom } = getMapViewport(stops, busCoord);
              return (
                <Camera
                  ref={cameraRef}
                  initialViewState={{
                    center,
                    zoom,
                  }}
                />
              );
            })()}
            {isScheduled && plannedCoords.length > 1 && (
              <GeoJSONSource
                id="mini-planned"
                data={{
                  type: 'Feature' as const,
                  geometry: { type: 'LineString' as const, coordinates: plannedCoords },
                  properties: {},
                }}
              >
                <Layer
                  id="mini-planned-line"
                  type="line"
                  paint={{
                    'line-color': '#94a3b8',
                    'line-width': 2.5,
                    'line-opacity': 0.5,
                  }}
                  layout={{
                    'line-cap': 'round',
                    'line-join': 'round',
                  }}
                />
              </GeoJSONSource>
            )}
            {isScheduled && trailCoords.length > 1 && (
              <GeoJSONSource id="mini-trail" data={trailGeoJSON}>
                <Layer
                  id="mini-trail-line"
                  type="line"
                  paint={{
                    'line-color': '#2563eb',
                    'line-width': 3.5,
                  }}
                  layout={{
                    'line-cap': 'round',
                    'line-join': 'round',
                  }}
                />
              </GeoJSONSource>
            )}
            {isScheduled && stops.map((stop: any) => (
              <Marker key={`ms-${stop.id}`} id={`ms-${stop.id}`} lngLat={[stop.lon, stop.lat]}>
                <View style={S.miniStopDot} />
              </Marker>
            ))}
            {(animatedBusCoord || busCoord) && (
              <Marker id="mini-bus" lngLat={animatedBusCoord || busCoord!} anchor="bottom">
                <View style={S.busPinContainer}>
                  <Image
                    source={
                      isLive
                        ? require('../assets/bus_green.png')
                        : require('../assets/bus_gray.png')
                    }
                    style={S.busPinImage}
                    resizeMode="contain"
                  />
                </View>
              </Marker>
            )}
          </Map>

          {/* Tap overlay */}
          <View style={S.mapOverlay}>
            <View style={S.tapPill}>
              <Text style={S.tapPillText}>Tap to view full map</Text>
            </View>
          </View>
        </TouchableOpacity>

        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const S = StyleSheet.create({
  safe:          { flex: 1, backgroundColor: Colors.bgGray },
  centeredFill:  { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 12 },
  loadingText:   { fontSize: 14, color: Colors.medGray },
  scrollContent: { paddingHorizontal: 20, paddingTop: 20, paddingBottom: 10 },

  // Header
  headerRow:     { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  screenTitle:   { fontSize: 26, fontWeight: '800', color: Colors.black },
  liveBadge:     { flexDirection: 'row', alignItems: 'center', backgroundColor: Colors.mintLighter, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20, gap: 6 },
  offlineBadge:  { backgroundColor: '#fee2e2' },
  liveDot:       { width: 8, height: 8, borderRadius: 4, backgroundColor: Colors.mintDeeper },
  offlineDot:    { backgroundColor: Colors.danger },
  liveText:      { fontSize: 12, fontWeight: '700', color: Colors.mintText },
  offlineText:   { color: Colors.danger },

  // Error
  errorBanner:     { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: '#fff5f5', borderRadius: 12, padding: 12, marginBottom: 14, borderWidth: 1, borderColor: '#fecaca' },
  errorBannerText: { fontSize: 13, color: Colors.danger, flex: 1 },

  // Trip row
  tripRow:         { flexDirection: 'row', alignItems: 'center', marginBottom: 16, gap: 6 },
  tripPill:        { backgroundColor: Colors.mintLighter, paddingHorizontal: 14, paddingVertical: 6, borderRadius: 20 },
  tripPillOffline: { backgroundColor: '#f3f4f6' },
  tripPillText:    { fontSize: 13, fontWeight: '800', color: Colors.mintText },
  tripPillTextOff: { color: Colors.medGray },
  tripArrow:       { marginHorizontal: 2 },
  tripDest:        { fontSize: 13, fontWeight: '700', color: Colors.darkGray, flex: 1 },

  // Stats card
  statsCard:       { backgroundColor: Colors.white, borderRadius: 18, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 18, paddingHorizontal: 20, marginBottom: 20, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.06, shadowRadius: 10, elevation: 3 },
  statItem:        { flex: 1, alignItems: 'center' },
  statValue:       { fontSize: 18, fontWeight: '800', color: Colors.black, marginBottom: 4 },
  statLabel:       { fontSize: 10, fontWeight: '600', color: Colors.lightGray, letterSpacing: 0.5 },
  statDivider:     { width: 1, height: 32, backgroundColor: Colors.separator },

  // Timeline card
  card:            { backgroundColor: Colors.white, borderRadius: 20, padding: 20, marginBottom: 24, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 12, elevation: 3 },
  emptyState:      { alignItems: 'center', paddingVertical: 24, gap: 10 },
  emptyStateText:  { fontSize: 13, color: Colors.medGray, textAlign: 'center', lineHeight: 20 },

  // Timeline rows (Where Is My Train style)
  timelineRow:        { flexDirection: 'row', minHeight: 52, alignItems: 'center', marginVertical: 3 },
  timeLeftCol:        { width: 70, alignItems: 'flex-start', justifyContent: 'center' },
  schedTimeText:      { fontSize: 13, fontWeight: '700', color: '#4b5563', marginBottom: 2 },
  liveTimeText:       { fontSize: 13, fontWeight: '800', color: '#16a34a' },
  timeTextLate:       { color: '#dc2626' },
  timeTextAhead:      { color: '#16a34a' },
  liveTimeTextMuted:  { fontSize: 13, fontWeight: '600', color: '#d1d5db' },

  timelineTrackCol:   { width: 32, alignItems: 'center', justifyContent: 'center', position: 'relative', alignSelf: 'stretch' },
  trackDot:           { width: 10, height: 10, borderRadius: 5, backgroundColor: '#9ca3af', zIndex: 2 },
  trackDotVisited:    { backgroundColor: '#2563eb', width: 12, height: 12, borderRadius: 6 },
  trackLine:          { position: 'absolute', top: '50%', bottom: -30, width: 3, backgroundColor: '#e5e7eb', zIndex: 1 },
  trackLineVisited:   { backgroundColor: '#2563eb' },
  busBadgeWrapper:    { width: 28, height: 28, borderRadius: 14, backgroundColor: 'rgba(37, 99, 235, 0.18)', alignItems: 'center', justifyContent: 'center', zIndex: 3 },
  busBadgeCircle:     { width: 22, height: 22, borderRadius: 11, backgroundColor: '#2563eb', alignItems: 'center', justifyContent: 'center', shadowColor: '#2563eb', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.35, shadowRadius: 4, elevation: 4 },

  stopInfoCol:        { flex: 1, paddingLeft: 12, justifyContent: 'center' },
  stopName:           { fontSize: 15, fontWeight: '700', color: '#1f2937', marginBottom: 2 },
  stopNameCurrent:    { color: '#2563eb', fontWeight: '800', fontSize: 15.5 },
  stopNameVisited:    { color: '#4b5563' },
  stopSubtext:        { fontSize: 11, fontWeight: '600', color: '#9ca3af' },
  stopSubtextCurrent: { color: '#2563eb', fontWeight: '700' },
  stopSubtextLate:    { color: '#dc2626', fontWeight: '700' },
  stopSubtextAhead:   { color: '#16a34a', fontWeight: '700' },

  // Section title
  sectionHeaderRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14, paddingHorizontal: 4 },
  sectionTitle:    { fontSize: 20, fontWeight: '800', color: Colors.black },
  lastUpdatedText: { fontSize: 12, color: Colors.medGray, fontWeight: '600' },

  // Map thumbnail
  mapCard:         { height: 180, borderRadius: 20, overflow: 'hidden', marginBottom: 10, position: 'relative', shadowColor: '#000', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.08, shadowRadius: 16, elevation: 6, backgroundColor: Colors.bgGray },
  miniMap:         { flex: 1 },
  miniStopDot:     { width: 8, height: 8, borderRadius: 4, backgroundColor: '#2563eb', borderWidth: 1.5, borderColor: Colors.white },
  
  // Bus Pin Marker (bus.svg)
  busPinContainer: { alignItems: 'center', justifyContent: 'center', width: 34, height: 34, borderRadius: 17, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.25, shadowRadius: 3, elevation: 5 },
  busPinImage:     { width: 34, height: 34 },

  mapOverlay:      { ...StyleSheet.absoluteFill, justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 16 },
  tapPill:         { backgroundColor: 'rgba(255,255,255,0.94)', paddingHorizontal: 22, paddingVertical: 10, borderRadius: 24, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.12, shadowRadius: 8, elevation: 4 },
  tapPillText:     { fontSize: 14, fontWeight: '700', color: Colors.black },
});
