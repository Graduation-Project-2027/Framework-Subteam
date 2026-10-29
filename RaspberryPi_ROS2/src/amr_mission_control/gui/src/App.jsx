import { Component, useState, useCallback, useRef, useEffect } from "react";
import { MotionConfig, AnimatePresence } from "motion/react";
import { useMission, useMediaQuery, readPreviewFlag, writePreviewFlag } from "./hooks/useMission";
import { useFleet } from "./hooks/useFleet";
import PanelTabs from "./components/PanelTabs";
import FleetShell from "./components/fleet/FleetShell";
import { useMapEditor } from "./hooks/useStack";
import { useDrive } from "./hooks/useDrive";
import { useStackPrefs } from "./lib/stackPrefs";
import { connectLane } from "./lib/lanes";
import TopBar from "./components/TopBar";
import MapView from "./components/map/MapView";
import TasksPanel from "./components/TasksPanel";
import LocationsPanel from "./components/LocationsPanel";
import MapsPanel from "./components/MapsPanel";
import RobotPanel from "./components/RobotPanel";
import EventLog from "./components/EventLog";
import ConnectionNotice from "./components/ConnectionNotice";
import SystemSheet from "./components/SystemSheet";
import { MappingBar, MapEditorBar } from "./components/MapOverlays";
import { ToastProvider } from "./components/Toasts";
import { useToast } from "./lib/toast";
import { Button, TipProvider } from "./components/ui";

class ErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error("[LEVERAGE]", error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash" role="alert">
        <h1>Mission control hit an error</h1>
        <p>The robot keeps its current task. Reload the screen to reconnect.</p>
        <pre>{String(this.state.error)}</pre>
        <Button variant="primary" onClick={() => window.location.reload()}>Reload</Button>
      </div>
    );
  }
}

function Shell({ target, workspace, onWorkspace, consoleLabel, fleetPreview, robots, onPickRobot, fleetLink }) {
  const m = useMission(target, fleetPreview);
  const toast = useToast();
  const wide = useMediaQuery("(min-width: 1280px)");
  const medium = useMediaQuery("(min-width: 900px)");
  const roomy = useMediaQuery("(min-width: 1440px)");
  const layout = wide ? "wide" : medium ? "medium" : "narrow";
  const disabled = !m.online;
  const mapping = m.stackMode === "mapping";

  const [prefs, setPrefs] = useStackPrefs();
  const [systemOpen, setSystemOpen] = useState(false);
  const { editor, open: openEditor } = useMapEditor(m.stack);
  const [tab, setTab] = useState("tasks");
  const [pick, setPick] = useState(null);
  const [lane, setLane] = useState(null);
  const [hoverId, setHoverId] = useState(null);
  const [laneHover, setLaneHover] = useState(null);
  const [focus, setFocus] = useState(null);
  const mapRef = useRef(null);
  const lanesRef = useRef([]);
  lanesRef.current = m.missionState.locations.lanes;

  const { sendCommand } = m;
  const drive = useDrive(sendCommand, disabled || !!editor);

  const revealMap = useCallback(() => {
    if (layout === "narrow") mapRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [layout]);

  const startPick = useCallback((label, cb) => {
    setLane(null);
    setPick({
      label,
      onPick: (p) => { cb(p); setPick(null); },
      onCancel: () => setPick(null),
    });
    revealMap();
  }, [revealMap]);

  const startLane = useCallback(() => {
    setPick(null);
    setLane({ points: [] });
    if (!m.layers.lanes) m.setLayer("lanes", true);
    revealMap();
  }, [revealMap, m]);
  const addLanePoint = useCallback((x, y) => setLane((d) => {
    if (!d) return d;
    const last = d.points[d.points.length - 1];
    if (last && Math.hypot(last.x - x, last.y - y) < 0.05) return d;
    return { points: [...d.points, { x, y }] };
  }), []);
  const undoLanePoint = useCallback(() => setLane((d) => (d ? { points: d.points.slice(0, -1) } : d)), []);
  const cancelLane = useCallback(() => setLane(null), []);
  const laneRef = useRef(null);
  laneRef.current = lane;
  const finishLane = useCallback(() => {
    const d = laneRef.current;
    if (d && d.points.length >= 2) {
      const existing = lanesRef.current || [];
      // Snap ends onto nearby lanes and add junction points at crossings,
      // so the new lane joins the network the robot routes on.
      const joined = connectLane(d.points, existing);
      sendCommand({
        action: "update_lanes",
        lanes: [...joined.lanes, { id: `lane_${Date.now()}`, name: `Lane ${existing.length + 1}`, points: joined.points }],
      });
    }
    laneRef.current = null;
    setLane(null);
  }, [sendCommand]);

  const focusSite = useCallback((s) => { setFocus({ x: s.x, y: s.y, key: Date.now() }); revealMap(); }, [revealMap]);

  const editMap = useCallback((info) => {
    setPick(null);
    setLane(null);
    openEditor(info).then(revealMap).catch((e) => toast({ title: "Map didn't open", body: e.message, tone: "error" }));
  }, [openEditor, revealMap, toast]);

  useEffect(() => {
    if (disabled) { setPick(null); setLane(null); }
  }, [disabled]);

  const ms = m.missionState;
  const tasks = { id: "tasks", label: "Tasks", badge: ms.queue.length || null, render: () => <TasksPanel missionState={ms} sendCommand={sendCommand} disabled={disabled} mapping={mapping} /> };
  const locations = {
    id: "locations", label: "Locations",
    render: () => (
      <LocationsPanel
        missionState={ms} sendCommand={sendCommand} disabled={disabled}
        onPick={startPick} onStartLane={startLane} laneActive={!!lane}
        hoverId={hoverId} onHoverId={setHoverId} onFocus={focusSite}
        laneHover={laneHover} onLaneHover={setLaneHover}
      />
    ),
  };
  const maps = { id: "maps", label: "Maps", render: () => <MapsPanel stack={m.stack} prefs={prefs} onEdit={editMap} editing={editor?.name} /> };
  const robot = { id: "robot", label: "Robot", render: () => <RobotPanel missionState={ms} sendCommand={sendCommand} disabled={disabled} drive={drive} /> };
  const log = { id: "log", label: "Log", render: () => <EventLog entries={m.feedbackLog} collapsible={false} /> };

  const leftTabs = layout === "wide" ? [tasks, locations, maps] : layout === "medium" ? [tasks, locations, maps, robot] : [tasks, locations, maps, robot, log];

  const map = (
    <div className="stage__map" ref={mapRef}>
      <MapView
        missionState={ms}
        plan={m.plan}
        sendCommand={sendCommand}
        live={m.live}
        layers={m.layers}
        onLayer={m.setLayer}
        stackMode={m.stackMode}
        pick={pick}
        laneDraft={lane}
        onAddLanePoint={addLanePoint}
        onUndoLanePoint={undoLanePoint}
        onFinishLane={finishLane}
        onCancelLane={cancelLane}
        hoverId={hoverId}
        onHoverId={setHoverId}
        laneHover={laneHover}
        focus={focus}
        offline={disabled}
        editor={editor}
      >
        {editor ? (
          <MapEditorBar editor={editor} />
        ) : (
          <>
            {mapping && m.online && <MappingBar stack={m.stack} live={m.live} prefs={prefs} />}
            <AnimatePresence>
              {disabled && (
                <ConnectionNotice
                  key="notice"
                  mode={m.mode}
                  status={m.status}
                  url={m.url}
                  retryAt={m.retryAt}
                  onRetry={m.retryNow}
                  onPreview={() => m.setPreview(true)}
                  stack={m.stack}
                  prefs={prefs}
                  onOpenSystem={() => setSystemOpen(true)}
                />
              )}
            </AnimatePresence>
          </>
        )}
      </MapView>
    </div>
  );

  return (
    <div className="app" data-layout={layout}>
      <a className="skip" href="#workspace">Skip to planning panel</a>
      <TopBar
        missionState={ms}
        mode={m.mode}
        status={m.status}
        online={m.online}
        url={m.url}
        sendCommand={sendCommand}
        onExitPreview={() => m.setPreview(false)}
        compact={!roomy}
        stack={m.stack}
        stackMode={m.stackMode}
        onOpenSystem={() => setSystemOpen(true)}
        workspace={workspace}
        onWorkspace={onWorkspace}
        consoleLabel={consoleLabel}
        robots={robots}
        currentHost={target?.host}
        onPickRobot={onPickRobot}
        fleetLink={fleetLink}
      />
      {layout === "narrow" ? (
        <>
          <main className="stage">{map}</main>
          <section className="sheet" id="workspace" aria-label="Planning and monitoring">
            <PanelTabs tabs={leftTabs} value={tab} onValueChange={setTab} label="Panels" />
          </section>
        </>
      ) : (
        <>
          <aside className="rail rail--left" id="workspace" aria-label="Planning">
            <PanelTabs tabs={leftTabs} value={tab} onValueChange={setTab} label="Planning" />
          </aside>
          <main className="stage">
            {map}
            <EventLog entries={m.feedbackLog} />
          </main>
          {layout === "wide" && (
            <aside className="rail rail--right" aria-label="Robot">
              <RobotPanel missionState={ms} sendCommand={sendCommand} disabled={disabled} drive={drive} />
            </aside>
          )}
        </>
      )}
      <SystemSheet open={systemOpen} onOpenChange={setSystemOpen} stack={m.stack} prefs={prefs} setPrefs={setPrefs} />
    </div>
  );
}

function readRoute() {
  const p = new URLSearchParams(window.location.search);
  const host = p.get("robot");
  return {
    workspace: p.get("view") === "robot" ? "robot" : "fleet",
    target: host ? { host, launcher: +p.get("launcher") || 8088, rosbridge: +p.get("rosbridge") || 9090, label: p.get("label") || host } : null,
  };
}

function writeRoute(workspace, target) {
  const url = new URL(window.location.href);
  ["view", "robot", "launcher", "rosbridge", "label"].forEach((k) => url.searchParams.delete(k));
  if (workspace === "robot") {
    url.searchParams.set("view", "robot");
    if (target) {
      url.searchParams.set("robot", target.host);
      if (target.launcher !== 8088) url.searchParams.set("launcher", target.launcher);
      if (target.rosbridge !== 9090) url.searchParams.set("rosbridge", target.rosbridge);
      if (target.label !== target.host) url.searchParams.set("label", target.label);
    }
  }
  window.history.replaceState(null, "", url.toString().replace("preview=", "preview"));
}

/** Fleet (the laptop's view of every robot) or one robot's console. */
function Workspaces() {
  const [route, setRoute] = useState(readRoute);
  const [preview, setPreviewState] = useState(readPreviewFlag);
  const fleet = useFleet(preview);

  const go = useCallback((workspace, target = route.target) => {
    writeRoute(workspace, target);
    setRoute({ workspace, target });
    // The robot console keeps its own preview switch; pick it up on return.
    if (workspace === "fleet") setPreviewState(readPreviewFlag());
  }, [route.target]);
  const setPreview = useCallback((on) => { writePreviewFlag(on); setPreviewState(on); }, []);

  const targetOf = (r) => ({ host: r.host, launcher: r.launcher || 8088, rosbridge: r.rosbridge || 9090, label: r.label });
  const openConsole = useCallback((r) => go("robot", targetOf(r)), [go]);
  const fleetRobots = fleet.state.robots.filter((r) => r.adopted);

  // In Preview the console always shows a fleet robot (the first, if none chosen).
  let target = route.target;
  if (preview && route.workspace === "robot" && !fleetRobots.some((r) => r.host === target?.host) && fleetRobots[0]) {
    target = targetOf(fleetRobots[0]);
  }
  const consoleLabel = target?.label || "Robot";

  // How this robot stands with the fleet: same map copy, layout in sync.
  const fleetLink = (() => {
    if (route.workspace !== "robot" || fleet.status !== "online") return null;
    const r = fleet.state.robots.find((x) => x.host === target?.host);
    if (!r || !r.adopted) return { inFleet: false };
    const lib = fleet.maps?.library.find((m) => m.name === fleet.fleetMap);
    const own = (fleet.maps?.robots?.[r.key] || []).find((m) => m.name === fleet.fleetMap);
    return {
      inFleet: true,
      map: fleet.fleetMap,
      running: r.stack?.running ? r.stack.map : null,
      sameCopy: !!(lib && own && own.fingerprint === lib.fingerprint),
      layout: r.layout_sync,
      rev: fleet.state.layout?.rev,
    };
  })();

  if (route.workspace === "robot") {
    return (
      <Shell
        key={`${preview ? "p" : "l"}:${target?.host || "local"}`}
        target={target}
        workspace="robot"
        onWorkspace={(w) => go(w)}
        consoleLabel={consoleLabel}
        fleetPreview={preview && fleet.client.kind === "preview" ? fleet.client : null}
        robots={fleetRobots}
        onPickRobot={(r) => go("robot", r ? targetOf(r) : null)}
        fleetLink={fleetLink}
      />
    );
  }
  return (
    <FleetShell
      fleet={fleet}
      workspace="fleet"
      onWorkspace={(w) => go(w)}
      consoleLabel={consoleLabel}
      onOpenConsole={openConsole}
      onPreview={() => setPreview(true)}
      onExitPreview={() => setPreview(false)}
    />
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <MotionConfig reducedMotion="user">
        <TipProvider>
          <ToastProvider>
            <Workspaces />
          </ToastProvider>
        </TipProvider>
      </MotionConfig>
    </ErrorBoundary>
  );
}
