// src/components/RoutePanel.jsx
import { useMemo } from 'react';
import StatusPill from './StatusPill';
import { cumulativeKm, fmtKm }                    from '../utils/geo';
import { agoFromIso, tripDestination, isReverseDirection } from '../utils/format';

const AVG_KMH    = 30;
const MS_PER_KMH = 3_600_000 / AVG_KMH; // ms per km — computed once

const fmtHHMM = (date) =>
  date.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true });

export default function RoutePanel({
  tripName, status, lateByMinutes, position, eta, stops, onRecentre,
}) {
  const isReverse  = isReverseDirection(tripName);
  const destination = tripDestination(tripName);
  const destShort   = isReverse ? 'CP' : 'DUK';

  // O(n) reverse only when stops/direction changes
  const stopsInDir = useMemo(
    () => (isReverse ? [...stops].reverse() : stops),
    [stops, isReverse]
  );
  const total   = stopsInDir.length;
  const visited = Math.min(position?.visited_stops ?? 1, total);

  // O(n) schedule computation, stable deps
  const schedule = useMemo(() => {
    if (!total) return [];
    const { cum } = cumulativeKm(stopsInDir);
    let anchorMs;
    if (position?.demo_mode) {
      anchorMs = Date.now() - (position.traveled_km ?? 0) * MS_PER_KMH;
    } else {
      const d = new Date();
      d.setHours(isReverse ? 17 : 7, 30, 0, 0);
      anchorMs = d.getTime();
    }
    return cum.map((c) => new Date(anchorMs + c * MS_PER_KMH));
  }, [stopsInDir, position?.traveled_km, position?.demo_mode, isReverse, total]);

  const progressPct = Math.min(100, position?.progress_pct ?? 0);
  const delayedText = lateByMinutes > 2 ? `Delayed +${lateByMinutes}m` : null;

  return (
    <aside className="route-panel">
      <div className="rp-handle" />

      {/* header */}
      <div className="rp-head">
        <div>
          <div className="rp-kicker">Live Route View</div>
          <div className="rp-title">Route View</div>
          <div className="rp-trip">
            <b>{tripName} Trip</b> → {destination}
            {lateByMinutes > 2 && (
              <span className="chip-late" style={{ marginLeft: 8 }}>
                Delayed {lateByMinutes}m
              </span>
            )}
          </div>
        </div>
        <StatusPill status={status} label="Live" />
      </div>

      {/* stats */}
      <div className="rp-stats">
        <div className="rp-stat">
          <div className="rp-stat-value rp-stat-value--eta">
            {eta?.minutes != null ? `${eta.minutes}m` : '—'}
          </div>
          <div className="rp-stat-label">ETA · TO {destShort}</div>
        </div>
        <div className="rp-stat-divider" />
        <div className="rp-stat">
          <div className="rp-stat-value">
            {position?.speed_kmh != null ? position.speed_kmh.toFixed(1) : '—'}
          </div>
          <div className="rp-stat-label">SPEED KM/H</div>
        </div>
        <div className="rp-stat-divider" />
        <div className="rp-stat">
          <div className="rp-stat-value">{visited}/{total}</div>
          <div className="rp-stat-label">STOPS</div>
        </div>
      </div>

      {/* progress */}
      <div className="rp-progress">
        <div className="rp-progress-top">
          <span>Trip progress</span>
          <span>
            {position?.progress_pct != null ? `${position.progress_pct}%` : '—'}
            {position?.remaining_km != null && ` · ${fmtKm(position.remaining_km)} left`}
          </span>
        </div>
        <div className="rp-progress-bar">
          <div className="rp-progress-fill" style={{ width: `${progressPct}%` }} />
        </div>
      </div>

      {/* stop timeline */}
      <div className="rp-timeline">
        {!position && (
          <div className="tl-row">
            <div className="tl-body">
              <div className="tl-name">Waiting for GPS fix…</div>
              <div className="tl-status">
                The bus reports its position shortly after departure.
              </div>
            </div>
          </div>
        )}
        {stopsInDir.map((stop, i) => {
          const isVisited  = i < visited;
          const isNext     = i === visited;
          const isTerminal = stop.order_index === 0 || stop.order_index === total - 1;
          const isOrigin   = (isReverse ? total - 1 : 0) === stop.order_index;

          // Derive label once per row
          let sub    = 'On time';
          let subCls = '';
          if (isVisited) {
            sub    = i === 0 ? 'Departed' : 'Arrived';
            subCls = 'tl-status--next';
          } else if (isNext) {
            sub    = 'Next Stop';
            subCls = 'tl-status--next';
          } else if (delayedText) {
            sub    = delayedText;
            subCls = 'tl-status--late';
          }

          const dotCls = [
            'tl-dot',
            isVisited  ? 'tl-dot--visited'  : '',
            isNext     ? 'tl-dot--next'     : '',
            isTerminal ? 'tl-dot--terminal' : '',
          ].filter(Boolean).join(' ');

          return (
            <div className="tl-row" key={stop.id}>
              <div className="tl-time">
                <div className="tl-sched">
                  {schedule[i] ? fmtHHMM(schedule[i]) : '—'}
                </div>
                {isVisited && <div className="tl-live">live</div>}
              </div>
              <div className="tl-track">
                <div className={dotCls} />
                {i < total - 1 && (
                  <div className={`tl-line${isVisited ? ' tl-line--visited' : ''}`} />
                )}
              </div>
              <div className="tl-body">
                <div className={`tl-name${isNext ? ' tl-name--next' : ''}`}>
                  {stop.name}
                  {isOrigin && <span className="tl-badge">START</span>}
                  {isTerminal && !isOrigin && (
                    <span className="tl-badge tl-badge--end">END</span>
                  )}
                </div>
                <div className={`tl-status ${subCls}`}>{sub}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* footer */}
      <div className="rp-foot">
        <span className="rp-live">
          <span className="live-dot" />
          Live · {agoFromIso(position?.updated_at)}
        </span>
        <button className="btn btn--ghost" onClick={onRecentre}>
          Recentre
        </button>
      </div>
    </aside>
  );
}
