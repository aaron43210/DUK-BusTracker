/**
 * timetable.js — DUK Bus Tracker PWA
 * Static daily scheduled timetables for morning and evening routes.
 * Matches the RN app's RouteViewScreen.tsx schedules exactly.
 */

export const MORNING_SCHEDULE = {
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

export const EVENING_SCHEDULE = {
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

export const EMAIL_DOMAINS = ['@duk.ac.in', '@iitmk.ac.in'];

export const DEFAULT_BUS_STOPS = [
  { id: 1,  name: 'Central Polytechnic',       desc: 'Starting point' },
  { id: 2,  name: 'Vattiyoorkavu Jn',          desc: 'Vattiyoorkavu junction' },
  { id: 5,  name: 'Sasthamangalam',            desc: 'Main road' },
  { id: 9,  name: 'Pattom',                    desc: 'Pattom palace junction' },
  { id: 10, name: 'Kesavadasapuram',           desc: 'Near MG College' },
  { id: 13, name: 'Sreekaryam',               desc: 'Main road junction' },
  { id: 15, name: 'Karyavattom',              desc: 'Near LNCPE' },
  { id: 17, name: 'Technopark Front',         desc: 'Technopark Phase 1' },
  { id: 18, name: 'Kazhakuttam',             desc: 'NH 66 bus stop' },
  { id: 20, name: 'Digital University Kerala', desc: 'Final stop — DUK campus' },
];

// ── Time Utilities ─────────────────────────────────────────────────────────

export function parseTimeToMinutes(timeStr) {
  if (!timeStr) return null;
  const match = timeStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!match) return null;
  let hours   = parseInt(match[1], 10);
  const mins  = parseInt(match[2], 10);
  const period = match[3].toUpperCase();
  if (period === 'PM' && hours !== 12) hours += 12;
  if (period === 'AM' && hours === 12) hours = 0;
  return hours * 60 + mins;
}

export function formatMinutesToTime(totalMins) {
  let m = ((totalMins % 1440) + 1440) % 1440;
  let hours = Math.floor(m / 60);
  const mins  = m % 60;
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
    const actMins   = parseTimeToMinutes(actualStr);
    const schedMins = parseTimeToMinutes(scheduledStr);
    if (actMins != null && schedMins != null) {
      const diff = actMins - schedMins;
      if (diff > 1)  return { text: `+${diff}m delay`, type: 'late',  diff };
      if (diff < -1) return { text: `${Math.abs(diff)}m ahead`, type: 'ahead', diff };
      return { text: 'On time', type: 'ahead', diff: 0 };
    }
  }
  if (globalLateMins != null) {
    if (globalLateMins > 0)  return { text: `+${globalLateMins}m delay`, type: 'late',  diff: globalLateMins };
    if (globalLateMins < 0)  return { text: `${Math.abs(globalLateMins)}m ahead`, type: 'ahead', diff: globalLateMins };
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
      if      (span > 1.0) zoom = 9;
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
