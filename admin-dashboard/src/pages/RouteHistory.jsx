// src/pages/RouteHistory.jsx
import React, { useState, useEffect, useRef, useCallback } from "react";
import { getRouteHistory, snapRoute } from "../api.js";
import { useToast } from "../App.jsx";
import { loadMapLibre, todayStr } from "../utils.js";
import { Search, MapPin, Play, Pause, RotateCcw, Clock } from "lucide-react";

// Premium Custom Time Picker Component
function PremiumTimePicker({ value, onChange }) {
    const [h24, m] = value ? value.split(':') : ['', ''];
    let h12 = '';
    let ampm = 'AM';

    if (h24) {
        let h = parseInt(h24, 10);
        ampm = h >= 12 ? 'PM' : 'AM';
        h = h % 12 || 12;
        h12 = h.toString().padStart(2, '0');
    }

    const selectStyle = {
        appearance: 'none', border: 'none', background: 'transparent',
        outline: 'none', fontSize: '15px', fontWeight: '500',
        color: 'var(--text)', cursor: 'pointer', textAlign: 'center',
        padding: '0 4px'
    };

    const handleHour = (e) => {
        if (!e.target.value) return onChange("");
        let newH = parseInt(e.target.value, 10);
        if (ampm === 'PM' && newH !== 12) newH += 12;
        if (ampm === 'AM' && newH === 12) newH = 0;
        onChange(`${newH.toString().padStart(2, '0')}:${m || '00'}`);
    };

    const handleMin = (e) => {
        if (!e.target.value) return onChange("");
        onChange(`${h24 || '12'}:${e.target.value}`);
    };

    const handleAmPm = (e) => {
        let newH = parseInt(h12 || '12', 10);
        if (e.target.value === 'PM' && newH !== 12) newH += 12;
        if (e.target.value === 'AM' && newH === 12) newH = 0;
        onChange(`${newH.toString().padStart(2, '0')}:${m || '00'}`);
    };

    return (
        <div style={{
            display: 'flex', gap: '2px', alignItems: 'center',
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: '0', padding: '8px 12px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.03)', width: 'max-content'
        }}>
            <select value={h12} onChange={handleHour} style={selectStyle}>
                <option value="">--</option>
                {[...Array(12)].map((_, i) => {
                    const val = (i + 1).toString().padStart(2, '0');
                    return <option key={val} value={val}>{val}</option>;
                })}
            </select>
            <span style={{ fontWeight: 'bold', color: 'var(--text-muted)' }}>:</span>
            <select value={m} onChange={handleMin} style={selectStyle}>
                <option value="">--</option>
                {[...Array(60)].map((_, i) => {
                    const val = i.toString().padStart(2, '0');
                    return <option key={val} value={val}>{val}</option>;
                })}
            </select>
            <select value={ampm} onChange={handleAmPm} style={{ ...selectStyle, color: 'var(--primary)', fontWeight: '600', marginLeft: '4px' }}>
                <option value="AM">AM</option>
                <option value="PM">PM</option>
            </select>
            <Clock size={16} style={{ color: 'var(--primary)', marginLeft: '6px' }} />
        </div>
    );
}


const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";

const BUS_SVG_RAW =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" width="40" height="40">' +
    '<rect x="3" y="7" width="34" height="22" rx="6" fill="#6366f1" stroke="white" stroke-width="2"/>' +
    '<rect x="6" y="10" width="11" height="8" rx="2" fill="rgba(255,255,255,0.9)"/>' +
    '<rect x="23" y="10" width="11" height="8" rx="2" fill="rgba(255,255,255,0.9)"/>' +
    '<circle cx="11" cy="32" r="4" fill="#1e1b4b" stroke="white" stroke-width="2"/>' +
    '<circle cx="29" cy="32" r="4" fill="#1e1b4b" stroke="white" stroke-width="2"/>' +
    "</svg>";

function createBusEl() {
    const el = document.createElement("div");
    el.innerHTML = BUS_SVG_RAW;
    el.style.cssText = "width:40px;height:40px;filter:drop-shadow(0 3px 8px rgba(0,0,0,.4))";
    return el;
}



export default function RouteHistory() {
    const showToast = useToast();

    // --- Search filters ---
    const [searchDate, setSearchDate] = useState(todayStr());
    const [startTime, setStartTime] = useState("");
    const [endTime, setEndTime] = useState("");

    // --- Search results ---
    const [session, setSession] = useState(null); // Just one session now!
    const [loading, setLoading] = useState(false);
    const [hasSearched, setHasSearched] = useState(false); // true only after user clicks Search

    // --- Selection + playback state ---
    const [playing, setPlaying] = useState(false);
    const [playIdx, setPlayIdx] = useState(0);
    const [speed, setSpeed] = useState(1); // 1x, 2x, 5x, 10x

    // --- Refs ---
    const playRef = useRef(null);
    const speedRef = useRef(1);
    speedRef.current = speed;
    const mapContRef = useRef(null);
    const mapRef = useRef(null);
    const readyRef = useRef(false);
    const busRef = useRef(null);
    const popRef = useRef(null);

    useEffect(() => {
        let d = false;
        loadMapLibre().then((ml) => {
            if (d || mapRef.current || !mapContRef.current) return;
            const m = new ml.Map({
                container: mapContRef.current,
                style: MAP_STYLE,
                center: [76.9366, 8.5241],
                zoom: 12,
                minZoom: 6,
                maxZoom: 18,
                maxBounds: [
                    [73.50, 7.50],
                    [84.50, 19.50]
                ]
            });
            m.on("load", () => {
                if (d) return;
                readyRef.current = true;
                mapRef.current = m;
            });
        });
        return () => {
            d = true;
            mapRef.current?.remove();
            mapRef.current = null;
            readyRef.current = false;
        };
    }, []);


    async function search() {
        if (!searchDate) {
            showToast("Select a date", "error");
            return;
        }
        setLoading(true);
        setHasSearched(true);
        stopPb();
        setSession(null);
        try {
            const data = await getRouteHistory(searchDate, searchDate);
            const sessList = data.sessions || [];

            if (!sessList.length) {
                showToast("No trips found on this date.", "error");
                return;
            }

            // We only have one "full_day" session per date from backend
            let rawSession = sessList[0];

            // Filter by time if provided
            let filteredPts = rawSession.route_points;
            let filteredStops = rawSession.stop_crossings;

            if (startTime) {
                filteredPts = filteredPts.filter(p => p.time >= startTime);
            }
            if (endTime) {
                filteredPts = filteredPts.filter(p => p.time <= endTime);
            }

            if (!filteredPts.length) {
                showToast("No GPS data found for the selected time range.", "error");
                return;
            }

            setSession({
                ...rawSession,
                route_points: filteredPts,
                stop_crossings: filteredStops
            });

        } catch (err) {
            showToast(err.message, "error");
        } finally {
            setLoading(false);
        }
    }



    const drawSess = useCallback((s) => {
        const map = mapRef.current;
        if (!map || !readyRef.current) return;

        ["rh-trail-line", "rh-stops"].forEach((id) => {
            if (map.getLayer(id)) map.removeLayer(id);
        });
        ["rh-trail", "rh-stops"].forEach((id) => {
            if (map.getSource(id)) map.removeSource(id);
        });
        if (busRef.current) {
            busRef.current.remove();
            busRef.current = null;
        }
        if (popRef.current) {
            popRef.current.remove();
            popRef.current = null;
        }

        const pts = s.route_points;
        if (!pts || !pts.length) {
            return;
        }
        const coords = pts.map((p) => [p.lon, p.lat]);

        map.addSource("rh-trail", {
            type: "geojson",
            data: { type: "Feature", geometry: { type: "LineString", coordinates: coords } },
        });
        map.addLayer({
            id: "rh-trail-line",
            type: "line",
            source: "rh-trail",
            layout: { "line-join": "round", "line-cap": "round" },
            paint: { "line-color": "#2563eb", "line-width": 5, "line-opacity": 0.9 },
        });

        // Snap the GPS trail using OSRM Map Matching with road centerline interpolation
        (async () => {
            try {
                const chunks = [];
                for (let i = 0; i < coords.length; i += 89) {
                    const chunk = coords.slice(i, i + 90);
                    if (chunk.length >= 2) {
                        chunks.push(chunk);
                    }
                }

                if (!chunks.length) return;

                const results = await Promise.all(chunks.map(chunk => snapRoute(chunk)));
                let snappedCoords = [];
                results.forEach((res, i) => {
                    if (res && res.coordinates && res.coordinates.length > 0) {
                        const pts = i > 0 ? res.coordinates.slice(1) : res.coordinates;
                        snappedCoords.push(...pts);
                    }
                });

                if (snappedCoords.length > 0 && map.getSource("rh-trail")) {
                    map.getSource("rh-trail").setData({
                        type: "Feature",
                        geometry: { type: "LineString", coordinates: snappedCoords }
                    });
                }
            } catch (err) {
                console.error("OSRM match snap failed:", err);
            }
        })();



        if (s.stop_crossings.length) {
            const groupedStops = {};
            s.stop_crossings.forEach((sc) => {
                if (!groupedStops[sc.stop_name]) {
                    groupedStops[sc.stop_name] = {
                        name: sc.stop_name,
                        lon: sc.lon,
                        lat: sc.lat,
                        times: [],
                    };
                }
                groupedStops[sc.stop_name].times.push(sc.crossed_at);
            });

            const feats = Object.values(groupedStops).map((g) => {
                return {
                    type: "Feature",
                    geometry: { type: "Point", coordinates: [g.lon, g.lat] },
                    properties: { name: g.name, time: g.times.join("<br>") },
                };
            });
            map.addSource("rh-stops", {
                type: "geojson",
                data: { type: "FeatureCollection", features: feats },
            });
            map.addLayer({
                id: "rh-stops",
                type: "circle",
                source: "rh-stops",
                paint: {
                    "circle-radius": 8,
                    "circle-color": "#f59e0b",
                    "circle-stroke-width": 2.5,
                    "circle-stroke-color": "#fff",
                },
            });

            const popup = new window.maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 });
            popRef.current = popup;
            map.on("mouseenter", "rh-stops", (e) => {
                map.getCanvas().style.cursor = "pointer";
                const { name, time } = e.features[0].properties;
                // Use DOM API instead of setHTML to prevent stored XSS from
                // database stop names or timestamps containing markup.
                const container = document.createElement("div");
                const nameEl = document.createElement("b");
                nameEl.textContent = name;
                const br = document.createElement("br");
                const timeEl = document.createElement("small");
                timeEl.textContent = String(time).replace(/<br>/g, " · ");
                container.appendChild(nameEl);
                container.appendChild(br);
                container.appendChild(timeEl);
                popup.setLngLat(e.lngLat).setDOMContent(container).addTo(map);
            });
            map.on("mouseleave", "rh-stops", () => {
                map.getCanvas().style.cursor = "";
                popup.remove();
            });
        }

        const el = createBusEl();
        busRef.current = new window.maplibregl.Marker({ element: el, anchor: 'bottom' }).setLngLat(coords[0]).addTo(map);

        const lngs = coords.map((c) => c[0]);
        const lats = coords.map((c) => c[1]);
        map.fitBounds(
            [
                [Math.min(...lngs) - 0.01, Math.min(...lats) - 0.01],
                [Math.max(...lngs) + 0.01, Math.max(...lats) + 0.01],
            ],
            { padding: 50, duration: 900 }
        );
    }, []);

    useEffect(() => {
        if (session) {
            stopPb();
            setPlayIdx(0);
            drawSess(session);
        } else {
            const map = mapRef.current;
            if (map && readyRef.current) {
                ["rh-trail-line", "rh-stops"].forEach((id) => {
                    if (map.getLayer(id)) map.removeLayer(id);
                });
                ["rh-trail", "rh-stops"].forEach((id) => {
                    if (map.getSource(id)) map.removeSource(id);
                });
                if (busRef.current) {
                    busRef.current.remove();
                    busRef.current = null;
                }
            }
        }
    }, [session, drawSess]);

    function stopPb() {
        clearInterval(playRef.current);
        setPlaying(false);
    }

    function startPb(overrideSpeed) {
        const pts = session?.route_points;
        if (!pts?.length) return;
        clearInterval(playRef.current);
        setPlaying(true);
        let i = playIdx;
        if (i >= pts.length - 1) {
            i = 0;
            setPlayIdx(0);
        }
        const currentSpeed = overrideSpeed ?? speedRef.current;
        const intervalMs = Math.max(12, Math.round(200 / currentSpeed));
        playRef.current = setInterval(() => {
            i++;
            if (i >= pts.length) {
                clearInterval(playRef.current);
                setPlaying(false);
                return;
            }
            setPlayIdx(i);
            busRef.current?.setLngLat([pts[i].lon, pts[i].lat]);
        }, intervalMs);
    }

    function handleSpeedChange(newSpeed) {
        setSpeed(newSpeed);
        if (playing) {
            startPb(newSpeed);
        }
    }

    function resetPb() {
        stopPb();
        setPlayIdx(0);
        const pts = session?.route_points;
        if (pts?.length) busRef.current?.setLngLat([pts[0].lon, pts[0].lat]);
    }

    useEffect(() => () => stopPb(), []);

    const total = session?.route_points?.length ?? 0;
    const pct = total > 1 ? (playIdx / (total - 1)) * 100 : 0;

    let curT = session?.route_points?.[playIdx]?.time ?? "--:--";
    if (curT !== "--:--") {
        const [h, m] = curT.split(":");
        let hours = parseInt(h, 10);
        const ampm = hours >= 12 ? "PM" : "AM";
        hours = hours % 12 || 12;
        curT = `${hours.toString().padStart(2, "0")}:${m} ${ampm}`;
    }

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px", height: "100%" }}>

            <div className="page-header">
                <div>
                    <div className="page-title">Route History</div>
                    <div className="page-sub">View and replay completed bus journeys on the map</div>
                </div>
            </div>

            <div className="card" style={{ padding: "12px 20px" }}>
                <div style={{ display: "flex", gap: "16px", alignItems: "flex-end", flexWrap: "wrap" }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Date</label>
                        <input
                            type="date"
                            className="form-input"
                            value={searchDate}
                            max={todayStr()}
                            onChange={(e) => setSearchDate(e.target.value)}
                            style={{ width: "160px" }}
                        />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Start Time</label>
                        <PremiumTimePicker value={startTime} onChange={setStartTime} />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">End Time</label>
                        <PremiumTimePicker value={endTime} onChange={setEndTime} />
                    </div>

                    <button className="btn btn-primary" onClick={search} disabled={loading} style={{ height: "38px", display: "flex", alignItems: "center" }}>
                        <Search size={15} />
                        <span style={{ marginLeft: "3px", fontSize: "15px" }}>{loading ? "Searching..." : "Search"}</span>
                    </button>
                </div>
            </div>

            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px" }}>
                {session && (
                    <div className="card" style={{ padding: "14px 18px", flexShrink: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap" }}>
                            <span style={{ fontWeight: 600, fontSize: "14px", minWidth: "70px", textAlign: "right" }}>{curT}</span>
                            <div
                                style={{
                                    flex: 1,
                                    height: "6px",
                                    background: "var(--border,#e5e7eb)",
                                    borderRadius: "3px",
                                    cursor: "pointer",
                                }}
                                onClick={(e) => {
                                    const rect = e.currentTarget.getBoundingClientRect();
                                    const ni = Math.round(((e.clientX - rect.left) / rect.width) * (total - 1));
                                    stopPb();
                                    setPlayIdx(ni);
                                    const pts = session.route_points;
                                    busRef.current?.setLngLat([pts[ni].lon, pts[ni].lat]);
                                }}
                            >
                                <div
                                    style={{
                                        height: "100%",
                                        borderRadius: "3px",
                                        background: "var(--primary,#6366f1)",
                                        width: pct + "%",
                                        transition: "width 0.15s linear",
                                    }}
                                />
                            </div>
                            {/* Speed Selector: 1x, 2x, 5x, 10x */}
                            <div
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    background: "var(--bg-subtle, #f3f4f6)",
                                    borderRadius: "3px",
                                    padding: "2px",
                                    gap: "2px",
                                    border: "1px solid var(--border, #e5e7eb)",
                                }}
                            >
                                {[1, 2, 5, 10].map((s) => (
                                    <button
                                        key={s}
                                        onClick={() => handleSpeedChange(s)}
                                        style={{
                                            border: "none",
                                            background: speed === s ? "var(--primary, #6366f1)" : "transparent",
                                            color: speed === s ? "#fff" : "var(--text-muted, #4b5563)",
                                            fontWeight: 700,
                                            fontSize: "12px",
                                            padding: "3px 8px",
                                            borderRadius: "2px",
                                            cursor: "pointer",
                                            transition: "all 0.15s ease",
                                        }}
                                    >
                                        {s}x
                                    </button>
                                ))}
                            </div>

                            <button className="btn btn-ghost btn-sm" onClick={resetPb} style={{ padding: "0 8px" }} title="Reset to start">
                                <RotateCcw size={16} />
                            </button>
                            <button className="btn btn-primary btn-sm" onClick={playing ? stopPb : () => startPb()} style={{ minWidth: "90px", display: "flex", justifyContent: "center", gap: "6px", alignItems: "center" }}>
                                {playing ? <><Pause size={16} /> Pause</> : <><Play size={16} /> Play</>}
                            </button>
                        </div>
                    </div>
                )}

                <div
                    style={{
                        borderRadius: "18px",
                        overflow: "hidden",
                        border: "1px solid var(--border,#e5e7eb)",
                        boxShadow: "0 8px 32px rgba(0,0,0,.06)",
                        minHeight: "550px",
                        flex: 1,
                        position: "relative",
                        background: "#e8ecf0",
                    }}
                >
                    {!session && hasSearched && !loading && (
                        <div
                            style={{
                                position: "absolute",
                                inset: 0,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                zIndex: 10,
                                pointerEvents: "none",
                            }}
                        >
                            <div
                                style={{
                                    background: "rgba(255,255,255,.85)",
                                    backdropFilter: "blur(12px)",
                                    borderRadius: "24px",
                                    padding: "36px 50px",
                                    textAlign: "center",
                                    boxShadow: "0 12px 48px rgba(0,0,0,.12)",
                                    border: "1px solid rgba(255,255,255,0.5)",
                                }}
                            >
                                <div style={{ fontWeight: 800, fontSize: "18px", marginBottom: "8px", color: "var(--text-dark)" }}>
                                    No GPS data found
                                </div>
                                <div style={{ fontSize: "14px", color: "var(--text-muted,#6b7280)", fontWeight: 500 }}>
                                    Search for a different date or adjust the time range
                                </div>
                            </div>
                        </div>
                    )}
                    <div ref={mapContRef} style={{ width: "100%", height: "100%", minHeight: "550px" }} />
                </div>

                {session && session.stop_crossings.length > 0 && (
                    <div className="card" style={{ padding: "0", overflow: "hidden", marginTop: "12px" }}>
                        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", background: "#f8fafc" }}>
                            <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
                                <MapPin size={18} color="var(--primary)" />
                                Stop Crossings ({session.stop_crossings.length})
                            </h3>
                        </div>
                        <div style={{ overflowX: "auto" }}>
                            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "14px", textAlign: "left" }}>
                                <thead>
                                    <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-muted)", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                                        <th style={{ padding: "12px 20px", width: "60px" }}>#</th>
                                        <th style={{ padding: "12px 20px" }}>Stop Name</th>
                                        <th style={{ padding: "12px 20px", width: "150px" }}>Crossed At</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {session.stop_crossings.map((sc, i) => (
                                        <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                                            <td style={{ padding: "12px 20px", color: "var(--text-muted)", fontWeight: 500 }}>{i + 1}</td>
                                            <td style={{ padding: "12px 20px", fontWeight: 600, color: "var(--text-dark)" }}>{sc.stop_name}</td>
                                            <td style={{ padding: "12px 20px" }}>
                                                <span style={{ background: "var(--bg-color)", padding: "4px 8px", borderRadius: "0", fontSize: "13px", fontWeight: 500 }}>
                                                    {sc.crossed_at}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}