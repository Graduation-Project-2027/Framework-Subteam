import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Box, Grid2x2, Route, Spline, Tag, Waypoints, FlaskConical, RefreshCw, Download, TriangleAlert, House, PencilLine } from "lucide-react";
import { Button, IconButton, Segmented } from "../ui";
import PanelTabs from "../PanelTabs";
import EventLog from "../EventLog";
import FleetTopBar from "./FleetTopBar";
import Roster, { SetupDialog } from "./Roster";
import RobotInspector from "./RobotInspector";
import DispatchPanel from "./DispatchPanel";
import LanesPanel from "./LanesPanel";
import MapSyncPanel from "./MapSyncPanel";
import ProvisionSheet from "./ProvisionSheet";
import { useMediaQuery } from "../../hooks/useMission";
import { useMapEditor } from "../../hooks/useStack";
import { MapEditorBar } from "../MapOverlays";
import { paintStroke, PAINT_VALUES } from "../../lib/edit";
import { connectLane, endTarget, endCanStayOpen, buildGraph, snapToNode, moveNode, deleteNode, siteFaces } from "../../lib/lanes";
import { useToast } from "../../lib/toast";
import { cx } from "../../lib/cx";

// three.js only loads with the fleet view.
const FleetScene = lazy(() => import("./FleetScene"));

// An end set to Connect joins a lane this close (a deliberate join needn't be pixel-exact).
const CONNECT_REACH = 1.0;

const LAYERS = [
  { id: "lanes", label: "Lanes", icon: Spline },
  { id: "paths", label: "Planned paths", icon: Route },
  { id: "claims", label: "Reserved floor", icon: Waypoints },
  { id: "labels", label: "Site names", icon: Tag },
];

function readLayers() {
  try { return { lanes: true, paths: true, claims: true, labels: true, ...JSON.parse(localStorage.getItem("leverage.fleet.layers") || "{}") }; } catch { return { lanes: true, paths: true, claims: true, labels: true }; }
}

function StageNotice({ fleet, onPreview }) {
  const toast = useToast();
  if (fleet.status === "offline" && !fleet.preview) {
    return (
      <motion.div className="notice" role="status" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22 }}>
        <h2 className="notice__title">The fleet manager isn’t running</h2>
        <p>
          The laptop runs the fleet: it finds robots, keeps them apart and syncs maps. Start it with{" "}
          <code translate="no">python3 src/amr_mission_control/fleet_server.py</code>. This screen connects on its own.
        </p>
        <p className="notice__how">Looking for <code translate="no">{fleet.client.url}</code>.</p>
        <div className="notice__actions">
          <Button variant="primary" icon={FlaskConical} onClick={onPreview}>Open preview</Button>
        </div>
        <p className="notice__foot">Preview runs three simulated robots on your real floor plan, with the same traffic rules. Nothing reaches hardware.</p>
      </motion.div>
    );
  }
  if (fleet.floor.missing) {
    const source = fleet.state.robots.find((r) => r.adopted && r.stack?.map === fleet.floor.missing);
    return (
      <motion.div className="notice" role="status" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
        <h2 className="notice__title">{fleet.floor.missing} isn’t in the fleet library</h2>
        <p>The fleet runs on {fleet.floor.missing}. Copy it to the laptop to see the floor in 3D and to send the same copy to every robot.</p>
        <div className="notice__actions">
          {source && (
            <Button variant="primary" icon={Download}
              onClick={() => fleet.act((c) => c.pull(source.key, fleet.floor.missing)).then(fleet.refreshMaps)
                .catch((e) => toast({ title: "Map not copied", body: e.message, tone: "error" }))}>
              Copy from {source.label}
            </Button>
          )}
        </div>
      </motion.div>
    );
  }
  return null;
}

function TrafficCard({ fleet, onSelect }) {
  const toast = useToast();
  const label = (k) => fleet.state.robots.find((r) => r.key === k)?.label || "?";
  const yields = fleet.state.traffic.conflicts.filter((c) => c.kind === "yield" || c.kind === "deadlock");
  const passes = fleet.state.traffic.conflicts.filter((c) => c.kind === "pass" || c.kind === "blocked");
  const dock = (key) => fleet.act((c) => c.command(key, { action: "return_to_dock" }))
    .then(() => toast({ title: `${label(key)} is heading to its dock` }))
    .catch((e) => toast({ title: "Command not sent", body: e.message, tone: "error" }));
  if (!yields.length && !passes.length) return null;
  return (
    <motion.section className="traffic" aria-label="Traffic" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
      <h2 className="traffic__title">Traffic</h2>
      <ul>
        {yields.map((c) => (
          <li key={`${c.a}-${c.b}`}>
            <button type="button" onClick={() => onSelect(c.b)} className={c.kind === "deadlock" ? "is-fault" : undefined}>
              <span className={`dot dot--${c.kind === "deadlock" ? "fault" : "caution"}`} />
              {c.kind === "deadlock"
                ? <><b>{label(c.a)}</b> and <b>{label(c.b)}</b> block each other</>
                : <><b>{label(c.b)}</b> holds for <b>{label(c.a)}</b></>}
              <span className="traffic__why">{c.kind === "deadlock" ? "drive one aside from its console" : c.reason}</span>
            </button>
          </li>
        ))}
        {passes.map((c) => (
          <li key={`${c.a}-${c.b}`} className={c.kind === "blocked" ? "traffic__blocked" : undefined}>
            <button type="button" onClick={() => onSelect(c.a)}>
              <span className={`dot dot--${c.kind === "blocked" ? "caution" : "idle"}`} />
              {c.kind === "blocked"
                ? <><b>{label(c.a)}</b> is stuck behind parked <b>{label(c.b)}</b></>
                : <><b>{label(c.a)}</b> passes parked <b>{label(c.b)}</b></>}
            </button>
            {c.kind === "blocked" && (
              <Button size="sm" variant="secondary" icon={House} onClick={() => dock(c.b)}>Dock {label(c.b)}</Button>
            )}
          </li>
        ))}
      </ul>
    </motion.section>
  );
}

export default function FleetShell({ fleet, workspace, onWorkspace, consoleLabel, onOpenConsole, onPreview, onExitPreview }) {
  const toast = useToast();
  const wide = useMediaQuery("(min-width: 1280px)");
  const medium = useMediaQuery("(min-width: 900px)");
  const roomy = useMediaQuery("(min-width: 1440px)");
  const layout = wide ? "wide" : medium ? "medium" : "narrow";

  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("robot");
  const [leftTab, setLeftTab] = useState("fleet");
  const [view, setView] = useState("iso");
  const [follow, setFollow] = useState(null);
  const [focus, setFocus] = useState(null);
  const [draft, setDraft] = useState(null);
  const [hoverLane, setHoverLane] = useState(null);
  const [setupKey, setSetupKey] = useState(null);
  const [layers, setLayers] = useState(readLayers);
  const [provOpen, setProvOpen] = useState(false);
  const [jobId, setJobId] = useState(null);
  const fleetAct = fleet.act;
  const deploy = useCallback((r) => fleetAct((c) => c.deploy(r.key))
    .then((res) => { setJobId(res.job); setProvOpen(true); })
    .catch((e) => toast({ title: "Not deployed", body: e.message, tone: "error" })), [fleetAct, toast]);
  const viewBeforeDraw = useRef("iso");
  const stageRef = useRef(null);

  useEffect(() => { try { localStorage.setItem("leverage.fleet.layers", JSON.stringify(layers)); } catch { /* private mode */ } }, [layers]);

  /* live map editing: the library copy, painted on the floor, then sent to
     every robot with that copy (robots navigating on it reload it live) */
  const { act, client: fleetClient } = fleet;
  const editStack = useMemo(() => ({
    client: { kind: `fleet-${fleetClient.kind}`, pgm: (n) => fleetClient.pgm(n) },
    putPgm: (name, bytes) => act((c) => c.putMapPgm(name, bytes)).then((res) => {
      const out = Object.values(res.results || {});
      const ok = out.filter((r) => r.ok);
      const live = ok.filter((r) => r.live).length;
      toast({ title: `${name} sent to ${ok.length} robot${ok.length === 1 ? "" : "s"}`,
        body: live ? `${live} navigating on it reloaded it live.` : "No robot navigates on it right now." });
      out.filter((r) => !r.ok).forEach((r) => toast({ title: "A robot kept its copy", body: r.error, tone: "error" }));
      fleet.refreshMaps();
      return res;
    }),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [act, fleetClient, toast]);
  const { editor, open: openEditor } = useMapEditor(editStack);
  const editorRef = useRef(null);
  editorRef.current = editor;
  const stroke = useRef(null);
  const [, repaint] = useState(0);
  const frame = useRef(0);
  const onPaint = useCallback((kind, x, y) => {
    const ed = editorRef.current;
    if (!ed) return;
    const p = { x, y }, v = PAINT_VALUES[ed.value];
    let changed = 0;
    if (kind === "down") {
      ed.begin();
      stroke.current = p;
      if (ed.tool === "brush") changed = paintStroke(ed.grid, p, p, ed.size / 2, v);
    } else if (kind === "move" && stroke.current && ed.tool === "brush") {
      changed = paintStroke(ed.grid, stroke.current, p, ed.size / 2, v);
      stroke.current = p;
    } else if (kind === "up") {
      if (stroke.current && ed.tool === "line") changed = paintStroke(ed.grid, stroke.current, p, ed.size / 2, v);
      stroke.current = null;
    }
    if (changed) {
      ed.changed();
      if (!frame.current) frame.current = requestAnimationFrame(() => { frame.current = 0; repaint((n) => n + 1); });
    }
  }, []);

  const robots = fleet.state.robots;
  const robot = robots.find((r) => r.key === selected) || null;
  useEffect(() => { if (selected && !robots.some((r) => r.key === selected)) setSelected(null); }, [robots, selected]);
  // Select the first robot once there is one, so the inspector has content.
  useEffect(() => {
    if (!selected) { const first = robots.find((r) => r.adopted); if (first) setSelected(first.key); }
  }, [robots, selected]);

  const draftRef = useRef(null);
  draftRef.current = draft;
  const select = useCallback((key) => {
    setSelected(key);
    // While a lane is being drawn the lane editor stays open.
    if (key && !draftRef.current) {
      setTab("robot");
      if (layout !== "wide") setLeftTab("robot");
    }
  }, [layout]);
  const locate = useCallback((key) => {
    setFocus({ key, n: Date.now() });
    if (layout === "narrow") stageRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [layout]);

  const startEdit = useCallback((info) => {
    if (!info) return;
    setDraft(null);
    setFollow(null);
    viewBeforeDraw.current = view;
    setView("top");
    openEditor(info).catch((e) => toast({ title: "Map didn't open", body: e.message, tone: "error" }));
  }, [openEditor, view, toast]);
  const floorInfo = fleet.maps?.library.find((m) => m.name === fleet.floor.name);
  useEffect(() => { if (!editor && viewBeforeDraw.current && view === "top" && !draft) setView(viewBeforeDraw.current); }, [editor]); // eslint-disable-line react-hooks/exhaustive-deps

  /* lane drawing */
  const startDraw = useCallback(() => {
    viewBeforeDraw.current = view;
    setView("top");
    setFollow(null);
    setDraft({ points: [], start: true, end: true });
    setLayers((l) => ({ ...l, lanes: true }));
  }, [view]);
  const endDraw = useCallback(() => { setDraft(null); setView(viewBeforeDraw.current); }, []);
  // Schematic drawing: a click near a lane node lands exactly on it (linking
  // the new lane there); a click on the lane's own first point closes it.
  const finishRef = useRef(null);
  const graphRef = useRef(null);
  const addPoint = useCallback((x, y) => {
    const d = draftRef.current;
    if (!d) return;
    const s = snapToNode({ x, y }, graphRef.current?.nodes, d);
    if (s?.close) { finishRef.current?.(true); return; }
    const p = s ? { x: s.x, y: s.y } : { x, y };
    const last = d.points[d.points.length - 1];
    if (last && Math.hypot(last.x - p.x, last.y - p.y) < 0.05) return;
    setDraft({ ...d, points: [...d.points, p] });
  }, []);
  const undoPoint = useCallback(() => setDraft((d) => (d ? { ...d, points: d.points.slice(0, -1) } : d)), []);
  const setEnd = useCallback((which, connect) => setDraft((d) => (d ? { ...d, [which]: connect } : d)), []);
  const finishDraw = useCallback((closed = false) => {
    const d = draftRef.current;
    const lay = fleet.layout;
    if (d && d.points.length >= (closed ? 3 : 2) && lay) {
      // Ends set to Connect snap onto the network; crossings become junctions.
      const joined = connectLane(d.points, lay.lanes || [], { start: d.start, end: d.end, closed, reach: CONNECT_REACH });
      const lanes = [...joined.lanes, { id: `lane_${Date.now()}`, name: `Lane ${(lay.lanes || []).length + 1}`, points: joined.points }];
      fleet.act((c) => c.putLayout({ ...lay, lanes }))
        .then((res) => { fleet.setLayout(res.layout); toast({ title: "Lane added", body: lay.sync ? "Sent to every robot in the fleet." : "Robot sync is off; the robots keep their lanes." }); })
        .catch((e) => toast({ title: "Lane not saved", body: e.message, tone: "error" }));
    }
    endDraw();
  }, [fleet, endDraw, toast]);
  finishRef.current = finishDraw;

  /* layout editing: lane nodes (drag, link, delete) and sites (place, aim) */
  const commitLayout = useCallback((patchLayout, title, body) => {
    const lay = fleet.layout;
    if (!lay) return Promise.resolve();
    return act((c) => c.putLayout({ ...lay, ...patchLayout }))
      .then((res) => { fleet.setLayout(res.layout); if (title) toast({ title, body }); })
      .catch((e) => toast({ title: "Layout not saved", body: e.message, tone: "error" }));
  }, [act, fleet, toast]);
  const [nodeMode, setNodeMode] = useState(false);
  const [selNode, setSelNode] = useState(null);
  const [dragLanes, setDragLanes] = useState(null);
  const [dragOn, setDragOn] = useState(false);
  const dragRef = useRef(null);
  const layoutLanes = fleet.layout?.lanes;
  const graph = useMemo(() => buildGraph(dragLanes || layoutLanes || []), [dragLanes, layoutLanes]);
  graphRef.current = graph;
  const onNodeDown = useCallback((i) => {
    setSelNode(i);
    dragRef.current = { from: graphRef.current.nodes[i], lanes: null };
    setDragOn(true);
  }, []);
  const onNodeDrag = useCallback((kind, x, y) => {
    const dr = dragRef.current;
    if (!dr) return;
    if (kind === "move") {
      dr.lanes = moveNode(fleet.layout?.lanes || [], dr.from, { x, y });
      setDragLanes(dr.lanes);
    } else if (kind === "up") {
      if (dr.lanes) {
        const linked = buildGraph(dr.lanes).nodes.length < buildGraph(fleet.layout?.lanes || []).nodes.length;
        commitLayout({ lanes: dr.lanes }, linked ? "Nodes linked" : "Node moved",
          linked ? "The two nodes are one now; the lanes join there." : undefined);
      }
      dragRef.current = null;
      setDragOn(false);
      setTimeout(() => setDragLanes(null), 300);
    }
  }, [fleet.layout, commitLayout]);
  const removeNode = useCallback(() => {
    const n = graph.nodes[selNode];
    if (!n) return;
    setSelNode(null);
    commitLayout({ lanes: deleteNode(fleet.layout?.lanes || [], n) }, "Node removed");
  }, [graph, selNode, fleet.layout, commitLayout]);

  const [placing, setPlacing] = useState(null); // { kind: "rack" | "zone" } or { move: id }
  const [hoverSite, setHoverSite] = useState(null);
  const onPlace = useCallback((x, y) => {
    const lay = fleet.layout;
    if (!lay || !placing) return;
    const yaw = siteFaces(lay.lanes, { x, y })[0]?.yaw ?? 0;
    const round = (v) => Math.round(v * 100) / 100;
    if (placing.kind) {
      const key = placing.kind === "rack" ? "racks" : "drop_zones";
      const prefix = placing.kind === "rack" ? "R" : "Z";
      const used = new Set([...lay.racks, ...lay.drop_zones].map((s) => s.name));
      let n = lay[key].length + 1;
      while (used.has(`${prefix}${n}`)) n += 1;
      const site = { id: `${key}_${Date.now()}`, name: `${prefix}${n}`, x: round(x), y: round(y), yaw: round(yaw),
        state: placing.kind === "rack" ? "occupied" : "free" };
      commitLayout({ [key]: [...lay[key], site] }, `${site.name} added`, "It faces the nearest lane; aim it at another from the list.");
    } else {
      const upd = (list) => list.map((s) => (s.id === placing.move ? { ...s, x: round(x), y: round(y), yaw: round(yaw) } : s));
      commitLayout({ racks: upd(lay.racks), drop_zones: upd(lay.drop_zones) }, "Site moved");
    }
    setPlacing(null);
  }, [fleet.layout, placing, commitLayout]);
  const startPlace = useCallback((spec) => {
    setDraft(null);
    setNodeMode(false);
    if (view !== "top") { viewBeforeDraw.current = view; setView("top"); }
    setPlacing(spec);
  }, [view]);
  const toggleNodes = useCallback((on) => {
    setDraft(null);
    setPlacing(null);
    setSelNode(null);
    if (on && view !== "top") { viewBeforeDraw.current = view; setView("top"); }
    if (!on && viewBeforeDraw.current && viewBeforeDraw.current !== "top") setView(viewBeforeDraw.current);
    setNodeMode(on);
  }, [view]);
  useEffect(() => {
    if (!nodeMode && !placing) return undefined;
    const onKey = (e) => {
      if (e.target.closest?.("input, textarea, select")) return;
      if (e.key === "Escape") { setPlacing(null); if (nodeMode) toggleNodes(false); }
      if (nodeMode && (e.key === "Delete" || e.key === "Backspace") && selNode != null) { e.preventDefault(); removeNode(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nodeMode, placing, selNode, removeNode, toggleNodes]);

  useEffect(() => {
    if (!draft) return undefined;
    const onKey = (e) => {
      if (e.target.closest?.("input, textarea, select")) return;
      if (e.key === "Escape") endDraw();
      else if (e.key === "Enter") finishDraw(false);
      else if (e.key === "Backspace") { e.preventDefault(); undoPoint(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [draft, endDraw, finishDraw, undoPoint]);

  // What each end of the lane being drawn will do, shown in the panel and on the floor.
  const draftEnds = useMemo(() => {
    if (!draft?.points.length) return null;
    const lanes = fleet.layout?.lanes || [];
    const describe = (p, connect) => {
      const t = endTarget(p, lanes, CONNECT_REACH);
      return {
        point: p, connect, target: t,
        joins: !!(connect && t),
        laneName: t?.lane?.name || "a lane",
        canStayOpen: endCanStayOpen(p, lanes),
      };
    };
    const pts = draft.points;
    return { start: describe(pts[0], draft.start), end: pts.length > 1 ? describe(pts[pts.length - 1], draft.end) : null };
  }, [draft, fleet.layout]);

  const events = useMemo(() => fleet.state.events.map((e) => ({ timestamp: e.t, message: e.text })), [fleet.state.events]);
  const holding = robots.filter((r) => r.hold).length;
  const newOnes = robots.filter((r) => !r.adopted && r.status === "online").length;

  const openConsole = useCallback((r) => onOpenConsole(r), [onOpenConsole]);
  const tabs = {
    fleet: { id: "fleet", label: "Fleet", badge: newOnes ? `${newOnes} new` : null, badgeTone: newOnes ? "gold" : null,
      render: () => <Roster fleet={fleet} selected={selected} onSelect={select} onProvision={() => setProvOpen(true)} provisioning={!!jobId} /> },
    robot: { id: "robot", label: "Robot", badge: robot?.hold ? "hold" : null, badgeTone: "gold",
      render: () => <RobotInspector robot={robot} robots={robots} fleet={fleet} onLocate={locate} follow={follow === robot?.key}
        onFollow={setFollow} onOpenConsole={openConsole} onSetup={setSetupKey} onDeploy={deploy} /> },
    dispatch: { id: "dispatch", label: "Dispatch", render: () => <DispatchPanel fleet={fleet} onSelect={select} /> },
    lanes: { id: "lanes", label: "Layout", render: () => (
      <LanesPanel fleet={fleet} draft={draft} ends={draftEnds} onSetEnd={setEnd} onStartDraw={startDraw} onUndo={undoPoint}
        onFinish={() => finishDraw(false)} onCloseLoop={() => finishDraw(true)} onCancel={endDraw}
        nodeMode={nodeMode} onNodeMode={toggleNodes} selectedNode={selNode != null ? { ...graph.nodes[selNode], deg: graph.adj[selNode]?.length } : null}
        onDeleteNode={removeNode} placing={placing} onPlace={startPlace} onCancelPlace={() => setPlacing(null)}
        hoverSite={hoverSite} onHoverSite={setHoverSite} commitLayout={commitLayout}
        hoverLane={hoverLane} onHoverLane={setHoverLane} />) },
    maps: { id: "maps", label: "Maps", render: () => <MapSyncPanel fleet={fleet} onEdit={startEdit} editing={editor?.name} /> },
  };
  const rightTabs = [tabs.robot, tabs.dispatch, tabs.lanes, tabs.maps];
  const leftTabs = layout === "wide" ? null : [tabs.fleet, tabs.robot, tabs.dispatch, tabs.lanes, tabs.maps];

  const stage = (
    <div className="stage__map scene3d" ref={stageRef}>
      <Suspense fallback={<div className="scene3d__loading" role="status">Loading the 3D floor…</div>}>
      <FleetScene
        grid={editor ? editor.grid : fleet.floor.grid}
        editor={editor}
        onPaint={onPaint}
        layout={dragLanes && fleet.layout ? { ...fleet.layout, lanes: dragLanes } : fleet.layout}
        nodeEdit={nodeMode ? { nodes: graph.nodes, deg: graph.adj.map((a) => a.length), selected: selNode, dragging: dragOn } : null}
        onNodeDown={onNodeDown}
        onNodeDrag={onNodeDrag}
        placing={!!placing}
        onPlace={onPlace}
        snapNodes={graph.nodes}
        faceFocus={hoverSite}
        robots={robots}
        conflicts={fleet.state.traffic.conflicts}
        selected={selected}
        onSelect={select}
        view={view}
        follow={follow}
        focus={focus}
        laneDraft={draft}
        laneEnds={draftEnds}
        onLanePoint={addPoint}
        onLaneFinish={() => finishDraw(false)}
        hoverLane={hoverLane}
        layers={layers}
      />
      </Suspense>
      <div className="scene3d__tools">
        <div className="scene3d__view">
          <Segmented size="sm" value={view} onValueChange={(v) => !draft && !editor && setView(v)} aria-label="Camera"
            options={[{ value: "iso", label: "3D", icon: Box }, { value: "top", label: "Plan", icon: Grid2x2 }]} />
        </div>
        <IconButton label={editor ? `Editing ${editor.name}` : "Edit the floor map live"} icon={PencilLine} size="sm"
          variant="float" className={cx(editor && "is-on")} aria-pressed={!!editor}
          disabled={!floorInfo || !!editor || !!draft} onClick={() => startEdit(floorInfo)} />
        <div className="scene3d__layers" role="group" aria-label="Show on the floor">
          {LAYERS.map((l) => (
            <IconButton key={l.id} label={`${layers[l.id] ? "Hide" : "Show"} ${l.label.toLowerCase()}`} icon={l.icon} size="sm"
              variant="float" className={cx(layers[l.id] && "is-on")} aria-pressed={layers[l.id]}
              onClick={() => setLayers((s) => ({ ...s, [l.id]: !s[l.id] }))} />
          ))}
        </div>
      </div>
      {editor && (
        <div className="scene3d__editbar">
          <MapEditorBar editor={editor} savedNote="Sent to every robot with this copy; robots navigating on it reload it live." />
        </div>
      )}
      {placing && (
        <div className="map__banner scene3d__banner" role="status">
          <Spline size={15} strokeWidth={1.75} aria-hidden="true" />
          <span>Click the floor where the {placing.kind === "zone" ? "drop zone" : placing.kind === "rack" ? "rack" : "site"} stands · it faces the nearest lane · <strong>Esc</strong> to cancel</span>
        </div>
      )}
      {nodeMode && !draft && (
        <div className="map__banner scene3d__banner" role="status">
          <Spline size={15} strokeWidth={1.75} aria-hidden="true" />
          <span>Drag a node to move it; drop it on another node to link them · <strong>Delete</strong> removes the selected node · <strong>Esc</strong> to finish</span>
        </div>
      )}
      {draft && (
        <div className="map__banner scene3d__banner" role="status">
          <Spline size={15} strokeWidth={1.75} aria-hidden="true" />
          <span>Click to place points · click a node to link to it · click the first point to close the loop · <strong>Enter</strong> to finish · <strong>Esc</strong> to cancel</span>
        </div>
      )}
      <div className="scene3d__right">
        <AnimatePresence><TrafficCard key="traffic" fleet={fleet} onSelect={select} /></AnimatePresence>
      </div>
      {follow && (
        <button type="button" className="scene3d__follow" onClick={() => setFollow(null)}>
          Following {robots.find((r) => r.key === follow)?.label} · stop
        </button>
      )}
      <div className="map__legend scene3d__legend" aria-label="Legend">
        <span><i className="sw sw--robot" />Robot</span>
        <span><i className="sw sw--live" />Driving</span>
        <span><i className="sw sw--hold" />Holding</span>
        <span><i className="sw sw--claim" />Reserved floor</span>
        <span><i className="sw sw--occ" />Rack</span>
        <span><i className="sw sw--free" />Free site</span>
        <span><i className="sw sw--lane" />Lane</span>
      </div>
      <AnimatePresence><StageNotice key="notice" fleet={fleet} onPreview={onPreview} /></AnimatePresence>
      {fleet.status === "connecting" && <div className="scene3d__loading" role="status"><RefreshCw size={15} strokeWidth={1.75} aria-hidden="true" /> Connecting to the fleet manager…</div>}
      {holding > 0 && layout === "narrow" && <div className="scene3d__narrowhold"><TriangleAlert size={14} aria-hidden="true" /> {holding} holding</div>}
    </div>
  );

  return (
    <div className="app app--fleet" data-layout={layout}>
      <a className="skip" href="#fleet-panels">Skip to fleet panels</a>
      <FleetTopBar fleet={fleet} workspace={workspace} onWorkspace={onWorkspace} consoleLabel={consoleLabel}
        onExitPreview={onExitPreview} compact={!roomy} onPickRobot={(r) => (r ? onOpenConsole(r) : onWorkspace("robot"))} />
      {layout === "narrow" ? (
        <>
          <main className="stage">{stage}</main>
          <section className="sheet" id="fleet-panels" aria-label="Fleet panels">
            <PanelTabs tabs={leftTabs} value={leftTab} onValueChange={setLeftTab} label="Fleet panels" />
          </section>
        </>
      ) : (
        <>
          <aside className="rail rail--left" id="fleet-panels" aria-label="Fleet">
            {layout === "wide"
              ? <div className="rail__scroll"><Roster fleet={fleet} selected={selected} onSelect={select} onProvision={() => setProvOpen(true)} provisioning={!!jobId} /></div>
              : <PanelTabs tabs={leftTabs} value={leftTab} onValueChange={setLeftTab} label="Fleet panels" />}
          </aside>
          <main className="stage">
            {stage}
            <EventLog entries={events} title="Fleet events" empty="Fleet events appear here: robots found, holds, map syncs, layout changes." />
          </main>
          {layout === "wide" && (
            <aside className="rail rail--right rail--inspector" aria-label="Selected robot and fleet tools">
              <PanelTabs tabs={rightTabs} value={tab} onValueChange={setTab} label="Fleet tools" />
            </aside>
          )}
        </>
      )}
      <ProvisionSheet open={provOpen} onOpenChange={setProvOpen} fleet={fleet} jobId={jobId} onJob={setJobId} onSelect={select} />
      <SetupDialog robot={robots.find((r) => r.key === setupKey)} robots={robots} open={!!setupKey}
        onOpenChange={(o) => !o && setSetupKey(null)} fleet={fleet} />
    </div>
  );
}
