// src/hooks/usePolling.js
import { useEffect, useRef, useState } from 'react';

export function usePolling(fn, intervalMs, enabled = true, deps = []) {
  const [data,   setData]   = useState(null);
  const [error,  setError]  = useState(null);
  const [online, setOnline] = useState(navigator.onLine);
  const fnRef = useRef(fn);
  fnRef.current = fn;          // always up-to-date, no re-render

  // Track online/offline once per mount — O(1) space
  useEffect(() => {
    const on  = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online',  on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online',  on);
      window.removeEventListener('offline', off);
    };
  }, []);

  // Polling loop — deps spread keeps identity stable
  useEffect(() => {
    if (!enabled || !online) return;
    let alive = true;

    const tick = async () => {
      try {
        const result = await fnRef.current();
        if (alive) { setData(result); setError(null); }
      } catch (err) {
        if (alive && err.name !== 'AbortError') setError(err);
      }
    };

    tick();
    const id = intervalMs ? setInterval(tick, intervalMs) : null;
    return () => {
      alive = false;
      if (id) clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, online, intervalMs, ...deps]);

  // Expose a manual refresh — stable reference, no useCallback needed
  const refresh = () => {
    fnRef.current().then(r => { setData(r); setError(null); })
                   .catch(err => { if (err.name !== 'AbortError') setError(err); });
  };

  return { data, error, online, refresh };
}
