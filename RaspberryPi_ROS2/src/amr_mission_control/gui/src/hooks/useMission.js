import { useEffect, useState, useCallback, useMemo, useRef, useSyncExternalStore } from "react";
import { useROS, rosUrl, WS_URL } from "./useROS";
import { useStack, useMapFile } from "./useStack";
import { PreviewEngine } from "../lib/previewEngine";
import { FleetConsole } from "../lib/fleetPreview";
import { DEFAULT_STATE } from "../lib/mission";
import { createLive } from "../lib/live";
import { createHttpStack } from "../lib/stackClient";
import { useLayers, sensorTopicsFor } from "../lib/layers";

const EMPTY = { missionState: DEFAULT_STATE, feedbackLog: [], plan: [], online: false, starting: true };

export function readPreviewFlag() {
  return new URLSearchParams(window.location.search).has("preview");
}

export function writePreviewFlag(on) {
  const url = new URL(window.location.href);
  if (on) url.searchParams.set("preview", "");
  else url.searchParams.delete("preview");
  window.history.replaceState(null, "", url.toString().replace("preview=", "preview"));
}

function usePreviewEngine(enabled, live, factory) {
  const [engine, setEngine] = useState(null);
  const [snap, setSnap] = useState(EMPTY);

  useEffect(() => {
    if (!enabled) { setEngine(null); setSnap(EMPTY); return undefined; }
    const e = factory ? factory() : new PreviewEngine(live);
    setEngine(e);
    const unsub = e.subscribe(setSnap);
    return () => { unsub(); live.reset(); live.mapFile = null; };
  }, [enabled, live, factory]);

  const send = useCallback((cmd) => engine?.send(cmd), [engine]);
  return { ...snap, engine, sendCommand: send };
}

/**
 * useMission — one data source for the whole UI: the live robot (rosbridge +
 * launcher_server.py) or the in-browser Preview, which simulates both.
 */
/**
 * `fleetPreview`: in Preview, the console shows that fleet robot (target.host)
 * instead of a robot of its own, so both views show the same robot.
 */
export function useMission(target = null, fleetPreview = null) {
  const [preview, setPreviewState] = useState(readPreviewFlag);
  const liveRef = useRef(null);
  if (!liveRef.current) liveRef.current = createLive();
  const live = liveRef.current;
  const [layers, setLayer] = useLayers();

  const host = target?.host;
  const launcherPort = target?.launcher;
  const http = useMemo(() => (host ? createHttpStack(`http://${host}:${launcherPort || 8088}`) : createHttpStack()), [host, launcherPort]);
  const wsUrl = host ? rosUrl(host, target.rosbridge || 9090) : WS_URL;
  const factory = useMemo(
    () => (fleetPreview && host ? () => new FleetConsole(fleetPreview, host) : null),
    [fleetPreview, host],
  );
  const sim = usePreviewEngine(preview, live, factory);
  const client = preview ? sim.engine?.api || null : http;
  const stack = useStack(client);
  const stackMode = stack.status?.running ? stack.status.mode : null;

  const ros = useROS(!preview, live, sensorTopicsFor(layers, stackMode), wsUrl);
  useMapFile(stack, live);

  const setPreview = useCallback((on) => {
    writePreviewFlag(on);
    setPreviewState(on);
  }, []);

  const src = preview ? sim : ros;
  const online = preview ? sim.online : ros.status === "connected";
  return {
    mode: preview ? "preview" : "live",
    status: preview ? (sim.online ? "preview" : "offline") : ros.status,
    online,
    retryAt: ros.retryAt,
    retryNow: ros.retryNow,
    url: ros.url,
    missionState: src.missionState,
    feedbackLog: src.feedbackLog,
    plan: src.plan,
    sendCommand: src.sendCommand,
    setPreview,
    live,
    layers,
    setLayer,
    stack,
    stackMode,
  };
}

export function useMediaQuery(query) {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
