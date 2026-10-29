/**
 * useFleet — the fleet manager (fleet_server.py on the laptop) or the
 * in-browser FleetPreview, behind one interface. Polls fleet state, keeps the
 * shared layout current, and loads the floor plan the fleet works on.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createHttpFleet } from "../lib/fleetClient";
import { FleetPreview } from "../lib/fleetPreview";
import { gridFromPGM } from "../lib/grid";

const POLL_MS = 250;
const EMPTY = { robots: [], traffic: { conflicts: [] }, warnings: [], events: [], layout: { rev: 0 } };

export function useFleet(preview) {
  const client = useMemo(() => (preview ? new FleetPreview() : createHttpFleet()), [preview]);
  useEffect(() => { client.start?.(); return () => client.stop?.(); }, [client]);

  const [state, setState] = useState(EMPTY);
  const [status, setStatus] = useState("connecting");
  const [layout, setLayout] = useState(null);
  const [maps, setMaps] = useState(null);
  const [floor, setFloor] = useState({ grid: null, name: null, missing: null });
  const failures = useRef(0);

  // Fleet state, polled. Poses are smoothed in the scene, not here.
  useEffect(() => {
    let alive = true, timer = null;
    setStatus("connecting");
    setState(EMPTY);
    const poll = async () => {
      try {
        const s = await client.state();
        if (!alive) return;
        failures.current = 0;
        setState(s);
        setStatus("online");
      } catch {
        if (!alive) return;
        failures.current += 1;
        if (failures.current >= 2) setStatus("offline");
      }
      if (alive) timer = setTimeout(poll, failures.current ? Math.min(4000, 500 * failures.current) : POLL_MS);
    };
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [client]);

  const rev = state.layout?.rev ?? 0;
  const refreshLayout = useCallback(() => client.getLayout().then(setLayout).catch(() => {}), [client]);
  useEffect(() => { if (status === "online") refreshLayout(); }, [rev, status, refreshLayout]);

  const refreshMaps = useCallback(() => client.maps().then(setMaps).catch(() => {}), [client]);
  useEffect(() => {
    if (status !== "online") return undefined;
    refreshMaps();
    const id = setInterval(refreshMaps, 5000);
    return () => clearInterval(id);
  }, [status, refreshMaps]);

  // The floor plan: the layout's map, else the map most of the fleet runs.
  const fleetMap = useMemo(() => {
    if (layout?.map) return layout.map;
    const counts = {};
    state.robots.forEach((r) => {
      if (r.adopted && r.stack?.running && r.stack.map) counts[r.stack.map] = (counts[r.stack.map] || 0) + 1;
    });
    return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] || null;
  }, [layout?.map, state.robots]);
  const libEntry = maps?.library.find((m) => m.name === fleetMap);
  const libKey = libEntry ? `${libEntry.name}:${libEntry.fingerprint}` : null;
  useEffect(() => {
    if (!fleetMap) { setFloor({ grid: null, name: null, missing: null }); return undefined; }
    if (!maps) return undefined;
    if (!libEntry) { setFloor({ grid: null, name: fleetMap, missing: fleetMap }); return undefined; }
    let alive = true;
    client.pgm(libEntry.name)
      .then((buf) => { if (alive) setFloor({ grid: gridFromPGM(buf, libEntry), name: libEntry.name, missing: null }); })
      .catch(() => { if (alive) setFloor({ grid: null, name: fleetMap, missing: fleetMap }); });
    return () => { alive = false; };
    // libKey captures the entry identity; libEntry itself changes every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, fleetMap, libKey, !!maps]);

  const act = useCallback(async (fn) => {
    const res = await fn(client);
    client.state().then(setState).catch(() => {});
    return res;
  }, [client]);

  return { client, preview, status, state, layout, setLayout, refreshLayout, maps, refreshMaps, floor, fleetMap, act };
}
