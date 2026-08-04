/**
 * MapFullScreen.tsx
 * Full-screen live map view for tracking the bus route.
 *
 * Shows:
 *  - Full-screen live map with road-snapped bus animation and permanent trail
 *  - Interactive stop pins with detailed stop overlay card
 *  - Floating map controls (re-center bus, zoom in/out)
 */

import React, { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  StatusBar,
  ActivityIndicator,
  Animated,
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

const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
const DEFAULT_CENTER: [number, number] = [76.848, 8.583];
const DEFAULT_ZOOM = 13;
const POLL_MS = 1500;

function getMapViewport(
  stops: any[],
  busCoord: [number, number] | null,
): { center: [number, number]; zoom: number } {
  if (busCoord) {
    return { center: busCoord, zoom: 14 };
  }
  if (stops && stops.length > 0) {
    const lons = stops.map(s => Number(s.lon)).filter(n => !isNaN(n));
    const lats = stops.map(s => Number(s.lat)).filter(n => !isNaN(n));
    if (lons.length > 0 && lats.length > 0) {
      const centerLon = (Math.min(...lons) + Math.max(...lons)) / 2;
      const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
      const span = Math.max(
        Math.max(...lons) - Math.min(...lons),
        Math.max(...lats) - Math.min(...lats),
      );
      let zoom = DEFAULT_ZOOM;
      if (span > 1.0) zoom = 9;
      else if (span > 0.5) zoom = 10;
      else if (span > 0.2) zoom = 11;
      else if (span > 0.1) zoom = 12;
      else zoom = 13;
      return { center: [centerLon, centerLat], zoom };
    }
  }
  return { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM };
}

export default function MapFullScreenView({ navigation }: any) {
  const [user, setUser] = useState<any>(null);
  const [tripState, setTripState] = useState<any>(null);
  const [busPosition, setBusPosition] = useState<any>(null);
  const [routeHistory, setRouteHistory] = useState<any>(null);
  const [eta, setEta] = useState<any>(null);
  const [plannedCoords, setPlannedCoords] = useState<[number, number][]>([]);
  const [trailCoords, setTrailCoords] = useState<[number, number][]>([]);
  const [stops, setStops] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedStop, setSelectedStop] = useState<any>(null);
  const [animatedBusCoord, setAnimatedBusCoord] = useState<[number, number] | null>(null);
  const [liveSegmentCoords, setLiveSegmentCoords] = useState<[number, number][]>([]);

  const mapRef = useRef<any>(null);
  const cameraRef = useRef<any>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const animIntervalRef = useRef<any>(null);
  const currentPosRef = useRef<[number, number] | null>(null);
  const zoomRef = useRef<number>(DEFAULT_ZOOM);
  const stopCardAnim = useRef(new Animated.Value(0)).current;

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
    if (!currentPosRef.current || distKm > 2.0) {
      setAnimatedBusCoord([targetLon, targetLat]);
      currentPosRef.current = [targetLon, targetLat];
      if (distKm > 2.0) setTrailCoords([]); // Clear trail on large teleports
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
    } catch (_) { }

    const cumDists = [0];
    for (let i = 1; i < polyline.length; i++) {
      const d = haversineDistKm(polyline[i - 1][0], polyline[i - 1][1], polyline[i][0], polyline[i][1]);
      cumDists.push(cumDists[i - 1] + d);
    }
    const totalDist = cumDists[cumDists.length - 1];

    if (totalDist <= 0.00001) {
      setAnimatedBusCoord([targetLon, targetLat]);
      currentPosRef.current = [targetLon, targetLat];
      return;
    }

    if (animIntervalRef.current) clearInterval(animIntervalRef.current);
    setLiveSegmentCoords([]);

    const animDurationMs = 2500;
    const startTime = Date.now();
    const fps = 25; // 25 updates per second

    // Smoothly pan camera to target position using hardware-accelerated easeTo
    if (isAutoCenterRef.current && cameraRef.current?.easeTo) {
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

      currentPosRef.current = [curLon, curLat];
      setAnimatedBusCoord([curLon, curLat]);

      const passedPts = polyline.slice(0, segIdx + 1) as [number, number][];
      passedPts.push([curLon, curLat]);
      setLiveSegmentCoords(passedPts);

      if (progress >= 1.0) {
        clearInterval(animIntervalRef.current);
        animIntervalRef.current = null;
        const finalPoint = polyline[polyline.length - 1] as [number, number];
        currentPosRef.current = finalPoint;
        setAnimatedBusCoord(finalPoint);

        // Append the exact OSRM road segment to the permanent trail
        const newPts = polyline.slice(1) as [number, number][];
        if (newPts.length > 0) {
          setTrailCoords(prev => [...prev, ...newPts]);
        } else {
          setTrailCoords(prev => [...prev, finalPoint]);
        }
      }
    }, 1000 / fps);
  };

  const [autoCenter, setAutoCenter] = useState(true);
  const isAutoCenterRef = useRef(true);

  useEffect(() => {
    isAutoCenterRef.current = autoCenter;
  }, [autoCenter]);

  // Handle manual map panning
  const onRegionWillChange = (event: any) => {
    const isUserInteraction = event?.properties?.isUserInteraction || event?.isUserInteraction;
    if (isUserInteraction && isAutoCenterRef.current) {
      setAutoCenter(false);
    }
  };

  const handleZoom = (delta: number) => {
    const next = Math.max(2, Math.min(19, zoomRef.current + delta));
    zoomRef.current = next;
    cameraRef.current?.zoomTo(next, { duration: 300 });
  };

  const recenterBus = () => {
    setAutoCenter(true); // User wants to re-center, so turn Auto-Center back on
    const targetCoord = animatedBusCoord || (busPosition?.lat ? [Number(busPosition.lon), Number(busPosition.lat)] : null);
    if (targetCoord && cameraRef.current) {
      cameraRef.current.flyTo({
        center: targetCoord as [number, number],
        zoom: 15,
        duration: 800,
      });
      zoomRef.current = 15;
    }
  };

  const handleSelectStop = (stop: any) => {
    setSelectedStop(stop);
    stopCardAnim.setValue(0);
    Animated.spring(stopCardAnim, {
      toValue: 1,
      useNativeDriver: true,
      tension: 65,
      friction: 9,
    }).start();

    if (cameraRef.current && stop?.lon && stop?.lat) {
      const stopCoord: [number, number] = [Number(stop.lon), Number(stop.lat)];
      cameraRef.current.flyTo({
        center: stopCoord,
        zoom: 15,
        duration: 600,
      });
      zoomRef.current = 15;
    }
  };

  const handleCloseStop = () => {
    Animated.timing(stopCardAnim, {
      toValue: 0,
      duration: 180,
      useNativeDriver: true,
    }).start(() => setSelectedStop(null));
  };

  useEffect(() => { getUser().then(u => { if (u) setUser(u); }); }, []);

  const fetchData = useCallback(async () => {
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
      if (stopsRes) setStops(stopsRes.data ?? []);
      if (geomRes?.data?.coordinates?.length) {
        setPlannedCoords(geomRes.data.coordinates);
      }

      // Load full trip history trace if scheduled; otherwise clear immediately
      if (isSched && tripData?.trip_id) {
        try {
          const traceRes = await trackingApi.getTripTrace(tripData.trip_id);
          if (traceRes?.data?.length) {
            setTrailCoords(traceRes.data);
          }
        } catch (_) { }
      } else if (!isSched) {
        setTrailCoords([]);
        setLiveSegmentCoords([]);
      }

      const stopId = user?.boarding_stop_id;
      if (stopId) {
        const etaRes = await trackingApi.getEta(stopId).catch(() => null);
        if (etaRes) setEta(etaRes.data);
      }
      // Only fly on first load if we don't have a position yet
      if (!currentPosRef.current && busRes?.data?.lat && cameraRef.current) {
        cameraRef.current.flyTo({
          center: [busRes.data.lon, busRes.data.lat] as [number, number],
          zoom: 14,
          duration: 1000,
        });
      }
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, [user]);

  useEffect(() => {
    fetchData();
    intervalRef.current = setInterval(fetchData, POLL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (animIntervalRef.current) clearInterval(animIntervalRef.current);
    };
  }, [fetchData]);

  // Derived
  const isLive = busPosition?.is_live === true;
  const rawTrip = tripState?.trip ?? '—';
  const tripName = rawTrip.toLowerCase() === 'forward' ? 'Morning' : (rawTrip.toLowerCase() === 'reverse' ? 'Evening' : (rawTrip.charAt(0).toUpperCase() + rawTrip.slice(1)));
  const status = tripState?.status ?? 'loading';
  // Scheduled trip check: morning or evening, not completed, not unscheduled/idle/offline
  const isScheduled = Boolean(
    (tripName.toLowerCase() === 'morning' || tripName.toLowerCase() === 'evening' || tripName.toLowerCase() === 'forward' || tripName.toLowerCase() === 'reverse' || tripState?.trip_id != null) &&
    tripName.toLowerCase() !== 'unscheduled' &&
    tripName.toLowerCase() !== 'idle' &&
    tripName.toLowerCase() !== 'offline' &&
    status !== 'completed'
  );

  const busCoord: [number, number] | null =
    busPosition?.lat != null && busPosition?.lon != null
      ? [Number(busPosition.lon), Number(busPosition.lat)]
      : null;

  const activeTrailCoords = useMemo(() => {
    return [...trailCoords, ...liveSegmentCoords];
  }, [trailCoords, liveSegmentCoords]);

  const trailGeoJSON = {
    type: 'Feature' as const,
    geometry: { type: 'LineString' as const, coordinates: activeTrailCoords },
    properties: {},
  };

  // Visited stops from routeHistory (only populated during active scheduled trips)
  const visitedStops: { name: string; time: string }[] =
    isScheduled && routeHistory
      ? Object.keys(routeHistory.visitedStops ?? {}).map(name => ({
        name,
        time: routeHistory.arrivalTimes?.[name] ?? '',
      }))
      : [];

  const currentStop = visitedStops[visitedStops.length - 1] ?? null;
  const etaMinutes = eta?.eta_minutes != null ? Math.round(eta.eta_minutes) : null;
  const visitedNames = new Set(visitedStops.map(s => s.name));

  return (
    <SafeAreaView style={S.safe} edges={['top']}>
      <StatusBar barStyle="dark-content" backgroundColor={Colors.bgGray} />
      <TopBar showBack onBack={() => navigation.goBack()} />

      {/* ── Full Map ──────────────────────────────────────────────────── */}
      <View style={S.mapWrapper}>
        {loading ? (
          <View style={S.mapLoading}>
            <ActivityIndicator size="large" color={Colors.mint} />
          </View>
        ) : (
          <Map
            ref={mapRef}
            style={S.map}
            mapStyle={MAP_STYLE}
            attributionPosition={{ bottom: 8, right: 8 }}
            onRegionWillChange={onRegionWillChange}
            onPress={() => { if (selectedStop) handleCloseStop(); }}
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

            {/* Planned Route Baseline (Scheduled Only) */}
            {isScheduled && plannedCoords.length > 1 && (
              <GeoJSONSource
                id="planned-route"
                data={{
                  type: 'Feature' as const,
                  geometry: { type: 'LineString' as const, coordinates: plannedCoords },
                  properties: {},
                }}
              >
                <Layer
                  id="planned-route-line"
                  type="line"
                  paint={{
                    'line-color': '#94a3b8',
                    'line-width': 3,
                    'line-opacity': 0.45,
                  }}
                  layout={{
                    'line-cap': 'round',
                    'line-join': 'round',
                  }}
                />
              </GeoJSONSource>
            )}

            {/* Trail (Scheduled Only) */}
            {isScheduled && activeTrailCoords.length > 1 && (
              <GeoJSONSource id="trail" data={trailGeoJSON}>
                <Layer
                  id="trail-line"
                  type="line"
                  paint={{
                    'line-color': '#2563eb',
                    'line-width': 4.5,
                    'line-opacity': 0.9,
                  }}
                  layout={{
                    'line-cap': 'round',
                    'line-join': 'round',
                  }}
                />
              </GeoJSONSource>
            )}

            {/* Interactive Stop Pins (Scheduled Only) */}
            {isScheduled && stops.map((stop: any, idx: number) => {
              const isSelected = selectedStop?.id === stop.id;
              const isVisited = visitedNames.has(stop.name);
              const isBoarding = user?.boarding_stop_id === stop.id;
              const stopNumber = stop.order_index != null ? stop.order_index + 1 : idx + 1;

              return (
                <Marker
                  key={`s-${stop.id}`}
                  id={`s-${stop.id}`}
                  lngLat={[Number(stop.lon), Number(stop.lat)]}
                  onPress={() => handleSelectStop(stop)}
                >
                  <TouchableOpacity
                    activeOpacity={0.8}
                    onPress={() => handleSelectStop(stop)}
                    hitSlop={{ top: 18, bottom: 18, left: 18, right: 18 }}
                    style={S.pinTouchArea}
                  >
                    {isSelected && <View style={S.pinSelectedGlow} />}
                    <View
                      style={[
                        S.interactivePin,
                        isVisited && S.pinVisited,
                        isBoarding && S.pinBoarding,
                        isSelected && S.pinSelected,
                      ]}
                    >
                      {isVisited ? (
                        <Ionicons name="checkmark" size={12} color="#ffffff" />
                      ) : isBoarding ? (
                        <Ionicons name="person" size={11} color="#ffffff" />
                      ) : (
                        <Text style={[S.pinNumberText, isSelected && S.pinNumberTextSelected]}>
                          {stopNumber}
                        </Text>
                      )}
                    </View>
                  </TouchableOpacity>
                </Marker>
              );
            })}

            {/* Bus marker — upright, straight, anchored at bottom to touch live road location */}
            {(animatedBusCoord || busCoord) && (
              <Marker id="bus-pin" lngLat={animatedBusCoord || busCoord!} anchor="bottom">
                <View style={S.busMarkerWrapper}>
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
                  <View style={[S.busMarkerTag, !isLive && S.busMarkerTagMuted]}>
                    <Text style={S.busMarkerTagText}>BUS</Text>
                  </View>
                </View>
              </Marker>
            )}
          </Map>
        )}

        {/* Floating Map Controls (Zoom In, Zoom Out, Recenter Bus) */}
        <View style={[S.mapControls, selectedStop ? { bottom: 220 } : null]} pointerEvents="box-none">
          {busCoord && (
            <>
              <TouchableOpacity style={S.mapControlBtn} activeOpacity={0.7} onPress={recenterBus}>
                <Ionicons name="locate" size={26} color={autoCenter ? '#2563eb' : '#9ca3af'} />
              </TouchableOpacity>
              <View style={S.mapControlDivider} />
            </>
          )}
          <TouchableOpacity
            style={S.mapControlBtn}
            activeOpacity={0.7}
            onPress={() => handleZoom(1)}
          >
            <Text style={S.zoomSymbolText}>+</Text>
          </TouchableOpacity>
          <View style={S.mapControlDivider} />
          <TouchableOpacity
            style={S.mapControlBtn}
            activeOpacity={0.7}
            onPress={() => handleZoom(-1)}
          >
            <Text style={S.zoomSymbolText}>−</Text>
          </TouchableOpacity>
        </View>
        {/* ── Stop Details Overlay Card (Appears ONLY when a stop is tapped) ── */}
        {selectedStop && (
          <Animated.View
            style={[
              S.selectedStopCard,
              {
                opacity: stopCardAnim,
                transform: [
                  {
                    translateY: stopCardAnim.interpolate({
                      inputRange: [0, 1],
                      outputRange: [180, 0],
                    }),
                  },
                ],
              },
            ]}
          >
            {(() => {
              const isVisited = visitedNames.has(selectedStop.name);
              const isCurrent = currentStop?.name === selectedStop.name;
              const passedTime = routeHistory?.arrivalTimes?.[selectedStop.name] || null;
              const scheduledTime = routeHistory?.scheduledTimes?.[selectedStop.name] ?? selectedStop.scheduled_time ?? null;
              const stopIndex = stops.findIndex(s => s.id === selectedStop.id);
              const stopNumber = selectedStop.order_index != null ? selectedStop.order_index + 1 : (stopIndex >= 0 ? stopIndex + 1 : 1);

              // Calculate approximate expected arrival time for upcoming stop
              let expectedDisplay = null;
              if (!isVisited) {
                const curIdx = currentStop ? stops.findIndex(s => s.name === currentStop.name) : -1;
                const stopsAhead = curIdx >= 0 ? Math.max(1, stopIndex - curIdx) : 1;
                const approxMins = etaMinutes != null && stopsAhead === 1 ? etaMinutes : (etaMinutes != null ? etaMinutes + (stopsAhead - 1) * 3 : stopsAhead * 4);
                const now = new Date();
                now.setMinutes(now.getMinutes() + approxMins);
                const expTimeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                expectedDisplay = `in ~${approxMins} min (${expTimeStr})`;
              }

              return (
                <View>
                  {/* Header */}
                  <View style={S.stopDetailHeader}>
                    <View style={S.stopDetailTitleRow}>
                      <View style={[S.stopDetailIconWrap, isVisited && S.stopDetailIconVisited]}>
                        <Ionicons name="location" size={20} color={isVisited ? Colors.mintDeeper : Colors.mintDark} />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={S.stopDetailName} numberOfLines={1}>{selectedStop.name}</Text>
                        <Text style={S.stopDetailSub}>Stop #{stopNumber} of {stops.length} • Route {selectedStop.route_id || 1}</Text>
                      </View>
                    </View>
                    <TouchableOpacity style={S.closeStopBtn} onPress={handleCloseStop} activeOpacity={0.7}>
                      <Ionicons name="close" size={20} color={Colors.darkGray} />
                    </TouchableOpacity>
                  </View>

                  {/* Status Badges & Info Cards */}
                  <View style={S.stopDetailStatusGrid}>
                    {/* Status Card */}
                    <View style={S.detailInfoCard}>
                      <Text style={S.detailInfoLabel}>STATUS</Text>
                      {isVisited ? (
                        <View style={S.statusPillPassed}>
                          <Ionicons name="checkmark-circle" size={14} color="#16a34a" />
                          <Text style={S.statusPillPassedText}>Crossed / Passed</Text>
                        </View>
                      ) : isCurrent ? (
                        <View style={S.statusPillCurrent}>
                          <Ionicons name="bus" size={14} color="#2563eb" />
                          <Text style={S.statusPillCurrentText}>Bus at Stop</Text>
                        </View>
                      ) : (
                        <View style={S.statusPillUpcoming}>
                          <Ionicons name="time" size={14} color="#d97706" />
                          <Text style={S.statusPillUpcomingText}>Upcoming Stop</Text>
                        </View>
                      )}
                    </View>

                    {/* Time Card */}
                    <View style={S.detailInfoCard}>
                      <Text style={S.detailInfoLabel}>{isVisited ? 'PASSED TIME' : 'EXPECTED ARRIVAL'}</Text>
                      <Text style={S.detailInfoValue}>
                        {isVisited
                          ? (passedTime || 'Passed earlier today')
                          : (expectedDisplay || 'Calculating...')}
                      </Text>
                    </View>
                  </View>

                  {/* Secondary details row (Scheduled time & Coordinates) */}
                  <View style={S.stopDetailMetaRow}>
                    <View style={S.stopMetaItem}>
                      <Ionicons name="calendar-outline" size={14} color={Colors.medGray} />
                      <Text style={S.stopMetaText}>
                        Scheduled: <Text style={{ fontWeight: '700', color: Colors.darkGray }}>{scheduledTime || '08:30 AM'}</Text>
                      </Text>
                    </View>
                    <View style={S.stopMetaItem}>
                      <Ionicons name="navigate-outline" size={14} color={Colors.medGray} />
                      <Text style={S.stopMetaText}>
                        {Number(selectedStop.lat).toFixed(4)}, {Number(selectedStop.lon).toFixed(4)}
                      </Text>
                    </View>
                  </View>
                </View>
              );
            })()}
          </Animated.View>
        )}
      </View>
    </SafeAreaView>
  );
}

const S = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bgGray },

  // Map
  mapWrapper: { flex: 1, position: 'relative', backgroundColor: Colors.bgGray },
  map: { flex: 1 },
  mapLoading: { flex: 1, justifyContent: 'center', alignItems: 'center' },

  // Floating Map Controls
  mapControls: { position: 'absolute', right: 16, bottom: 24, width: 44, backgroundColor: '#ffffff', borderRadius: 14, borderWidth: 1, borderColor: 'rgba(0,0,0,0.08)', shadowColor: '#000', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.18, shadowRadius: 8, elevation: 12, zIndex: 9999, overflow: 'hidden' },
  mapControlBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center', backgroundColor: '#ffffff' },
  mapControlDivider: { height: 1, width: 28, backgroundColor: '#e5e7eb', alignSelf: 'center' },
  zoomSymbolText: { fontSize: 24, fontWeight: '600', color: '#1f2937', lineHeight: 26, textAlign: 'center', includeFontPadding: false },

  // Interactive Pin Markers
  pinTouchArea: { alignItems: 'center', justifyContent: 'center', width: 36, height: 36 },
  pinSelectedGlow: { position: 'absolute', width: 34, height: 34, borderRadius: 17, backgroundColor: 'rgba(37, 99, 235, 0.25)', borderWidth: 2, borderColor: '#2563eb' },
  interactivePin: { width: 22, height: 22, borderRadius: 11, backgroundColor: '#2563eb', justifyContent: 'center', alignItems: 'center', borderWidth: 2, borderColor: '#ffffff', shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.25, shadowRadius: 3, elevation: 4 },
  pinVisited: { backgroundColor: '#64748b' },
  pinBoarding: { backgroundColor: '#16a34a' },
  pinSelected: { backgroundColor: Colors.mintDeeper, width: 26, height: 26, borderRadius: 13, borderWidth: 2.5 },
  pinNumberText: { fontSize: 10, fontWeight: '800', color: '#ffffff', textAlign: 'center', includeFontPadding: false },
  pinNumberTextSelected: { fontSize: 11 },

  // Selected Stop Details Overlay Card (Positioned at bottom of map)
  selectedStopCard: { position: 'absolute', bottom: 0, left: 0, right: 0, backgroundColor: '#ffffff', borderTopLeftRadius: 28, borderTopRightRadius: 28, padding: 20, paddingBottom: 34, shadowColor: '#000', shadowOffset: { width: 0, height: -6 }, shadowOpacity: 0.18, shadowRadius: 20, elevation: 999, zIndex: 99999, borderTopWidth: 1, borderColor: 'rgba(0,0,0,0.06)' },
  stopDetailHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 },
  stopDetailTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 10, flex: 1 },
  stopDetailIconWrap: { width: 38, height: 38, borderRadius: 12, backgroundColor: '#eff6ff', justifyContent: 'center', alignItems: 'center' },
  stopDetailIconVisited: { backgroundColor: Colors.mintLighter },
  stopDetailName: { fontSize: 17, fontWeight: '800', color: Colors.darkGray, letterSpacing: -0.2 },
  stopDetailSub: { fontSize: 12, fontWeight: '600', color: Colors.medGray, marginTop: 2 },
  closeStopBtn: { width: 32, height: 32, borderRadius: 16, backgroundColor: '#f3f4f6', justifyContent: 'center', alignItems: 'center' },

  stopDetailStatusGrid: { flexDirection: 'row', gap: 10, marginBottom: 12 },
  detailInfoCard: { flex: 1, backgroundColor: '#f9fafb', borderRadius: 14, padding: 12, borderWidth: 1, borderColor: '#f3f4f6' },
  detailInfoLabel: { fontSize: 10, fontWeight: '700', color: Colors.medGray, letterSpacing: 0.6, marginBottom: 6 },
  detailInfoValue: { fontSize: 14, fontWeight: '800', color: Colors.darkGray },

  statusPillPassed: { flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: '#dcfce7', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8, alignSelf: 'flex-start' },
  statusPillPassedText: { fontSize: 11, fontWeight: '800', color: '#15803d' },
  statusPillCurrent: { flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: '#dbeafe', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8, alignSelf: 'flex-start' },
  statusPillCurrentText: { fontSize: 11, fontWeight: '800', color: '#1d4ed8' },
  statusPillUpcoming: { flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: '#fef3c7', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8, alignSelf: 'flex-start' },
  statusPillUpcomingText: { fontSize: 11, fontWeight: '800', color: '#b45309' },

  stopDetailMetaRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingTop: 8, borderTopWidth: 1, borderTopColor: '#f3f4f6' },
  stopMetaItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  stopMetaText: { fontSize: 12, color: Colors.medGray },

  // Bus Pin Marker
  busMarkerWrapper: { alignItems: 'center', justifyContent: 'center' },
  busPinContainer: { alignItems: 'center', justifyContent: 'center', width: 38, height: 38, borderRadius: 19, shadowColor: '#000', shadowOffset: { width: 0, height: 3 }, shadowOpacity: 0.3, shadowRadius: 4, elevation: 6 },
  busPinImage: { width: 38, height: 38 },
  busMarkerTag: { marginTop: 1, backgroundColor: '#16a34a', paddingHorizontal: 7, paddingVertical: 1.5, borderRadius: 8, borderWidth: 1.5, borderColor: '#ffffff', shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.2, shadowRadius: 2, elevation: 4 },
  busMarkerTagMuted: { backgroundColor: '#6b7280' },
  busMarkerTagText: { fontSize: 9, fontWeight: '800', color: '#ffffff', letterSpacing: 0.5 },
});
