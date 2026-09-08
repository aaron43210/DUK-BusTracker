/**
 * timetable.js — DUK Bus Tracker PWA
 *
 * Scheduled arrival times are now stored in the backend DB on each BusStop
 * (morning_time / evening_time columns) and served live via /api/v1/stops.
 *
 * DEFAULT_BUS_STOPS is used ONLY as a last-resort offline fallback if the API
 * is unreachable on first load. These values must be kept in sync with the DB
 * — but in practice the API should always be available after the first load
 * because the PWA caches the response via the service worker.
 */

export const EMAIL_DOMAINS = ['@duk.ac.in', '@iiitmk.ac.in'];



//  Time Utilities 

export function parseTimeToMinutes(timeStr) {
  if (!timeStr) return null;
  const match = timeStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!match) return null;
  let hours = parseInt(match[1], 10);
  const mins = parseInt(match[2], 10);
  const period = match[3].toUpperCase();
  if (period === 'PM' && hours !== 12) hours += 12;
  if (period === 'AM' && hours === 12) hours = 0;
  return hours * 60 + mins;
}

export function formatMinutesToTime(totalMins) {
  let m = ((totalMins % 1440) + 1440) % 1440;
  let hours = Math.floor(m / 60);
  const mins = m % 60;
  const period = hours >= 12 ? 'PM' : 'AM';
  if (hours > 12) hours -= 12;
  if (hours === 0) hours = 12;
  return `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')} ${period}`;
}

export function computeEstimatedTime(scheduledStr, delayMinutes) {
  const schedMins = parseTimeToMinutes(scheduledStr);
  if (schedMins == null) return null;
  return formatMinutesToTime(schedMins + delayMinutes);
}

export function getDelayBadge(actualStr, scheduledStr, globalLateMins) {
  if (actualStr && scheduledStr) {
    const actMins = parseTimeToMinutes(actualStr);
    const schedMins = parseTimeToMinutes(scheduledStr);
    if (actMins != null && schedMins != null) {
      const diff = actMins - schedMins;
      if (diff > 1) return { text: `+${diff}m delay`, type: 'late', diff };
      if (diff < -1) return { text: `${Math.abs(diff)}m ahead`, type: 'ahead', diff };
      return { text: 'On time', type: 'ahead', diff: 0 };
    }
  }
  if (globalLateMins != null) {
    if (globalLateMins > 0) return { text: `+${globalLateMins}m delay`, type: 'late', diff: globalLateMins };
    if (globalLateMins < 0) return { text: `${Math.abs(globalLateMins)}m ahead`, type: 'ahead', diff: globalLateMins };
    return { text: 'On time', type: 'ahead', diff: 0 };
  }
  return null;
}

export function haversineDistKm(lon1, lat1, lon2, lat2) {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
    Math.cos((lat2 * Math.PI) / 180) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function getMapViewport(stops, busCoord) {
  if (busCoord) return { center: busCoord, zoom: 14 };
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
      let zoom = 13;
      if (span > 1.0) zoom = 9;
      else if (span > 0.5) zoom = 10;
      else if (span > 0.2) zoom = 11;
      else if (span > 0.1) zoom = 12;
      return { center: [centerLon, centerLat], zoom };
    }
  }
  return { center: [76.9366, 8.5241], zoom: 12 };
}

export function todayStr() {
  return new Date().toISOString().split('T')[0];
}
