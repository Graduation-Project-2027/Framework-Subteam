import { useState, memo } from "react";
import { Switch } from "radix-ui";
import { Spline, Trash2, Download, Check, Undo2, X, RefreshCw, Repeat, MoveRight, ArrowLeftRight, Waypoints, Plus, Move, Compass } from "lucide-react";
import { Button, IconButton, Slider, Menu, MenuTrigger, MenuContent, MenuItem, MenuLabel, ConfirmDialog, Kbd, Segmented } from "../ui";
import { RobotGlyph } from "./common";
import { fmt } from "../../lib/geometry";
import { buildGraph, MERGE_DIST, siteFaces } from "../../lib/lanes";
import { useToast } from "../../lib/toast";
import { cx } from "../../lib/cx";

/** loop | joined | n open ends, as the robot's lane graph sees it. */
function laneShape(lane, graph) {
  const pts = lane.points;
  const a = pts[0], b = pts[pts.length - 1];
  const dir = lane.oneway ? "one-way " : "";
  if (pts.length >= 3 && Math.hypot(a.x - b.x, a.y - b.y) < MERGE_DIST) return `${dir}loop`;
  const deg = (p) => {
    const i = graph.nodes.findIndex((n) => Math.hypot(n.x - p.x, n.y - p.y) < MERGE_DIST);
    return i < 0 ? 0 : graph.adj[i].length;
  };
  const open = [a, b].filter((p) => deg(p) <= 1).length;
  return `${dir}${open ? `${open} open end${open > 1 ? "s" : ""}` : "joined"}`;
}

function EndRow({ label, end, onChange }) {
  if (!end) return null;
  const status = end.joins
    ? `Joins ${end.laneName}`
    : end.connect
      ? "Open end: no lane within 1 m"
      : end.canStayOpen ? "Open end" : `Too close to ${end.laneName} to stay open; it will join it`;
  return (
    <div className="drawend">
      <span className="drawend__label">{label}</span>
      <span className={cx("drawend__status", !end.connect && !end.canStayOpen && "is-warn")}>{status}</span>
      <Segmented size="sm" value={end.connect ? "connect" : "open"} onValueChange={(v) => onChange(v === "connect")}
        aria-label={`${label} of the lane`}
        options={[{ value: "connect", label: "Connect" }, { value: "open", label: "Open" }]} />
    </div>
  );
}

const length = (pts) => pts.reduce((d, p, i) => (i ? d + Math.hypot(p.x - pts[i - 1].x, p.y - pts[i - 1].y) : 0), 0);

/** The lane a site faces, if its face is aimed square at one. */
function facing(site, lanes) {
  const yaw = site.yaw || 0;
  const faces = siteFaces(lanes, site);
  const i = faces.findIndex((f) => Math.abs(Math.atan2(Math.sin(f.yaw - yaw), Math.cos(f.yaw - yaw))) < 0.06);
  return { faces, i, lane: i >= 0 ? faces[i].lane : null };
}

function SiteRow({ site, kind, lanes, onChange, onRemove, onMove, hover, onHover }) {
  const [name, setName] = useState(site.name);
  const { faces, i, lane } = facing(site, lanes);
  const deg = Math.round(((site.yaw || 0) * 180) / Math.PI);
  const commitName = () => { const n = name.trim(); if (n && n !== site.name) onChange({ name: n }); else setName(site.name); };
  return (
    <li className={cx("siterow", hover && "is-hovered")} onMouseEnter={() => onHover(site.id)} onMouseLeave={() => onHover(null)}>
      <span className={cx("glyph", site.state === "occupied" ? "glyph--occ" : "glyph--free", kind === "zone" && "glyph--zone")} />
      <input className="siterow__name" value={name} onChange={(e) => setName(e.target.value)} onBlur={commitName}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") { setName(site.name); e.currentTarget.blur(); } }}
        aria-label={`Name of ${site.name}`} maxLength={12} />
      <span className="siterow__face">
        {lane ? <>faces <b>{lane.name || "a lane"}</b></> : faces.length ? <span className="tone--caution">not square to a lane · {deg}°</span> : "no lane in front"}
      </span>
      <span className="site__actions">
        <IconButton label={faces.length > 1 ? "Face the next lane" : "Face the lane squarely"} icon={Compass} variant="ghost" size="sm"
          disabled={!faces.length} onClick={() => { const f = faces[(i + 1) % faces.length]; onChange({ yaw: Math.round(f.yaw * 1000) / 1000 }); }} />
        <IconButton label={`Move ${site.name}`} icon={Move} variant="ghost" size="sm" onClick={onMove} />
        <IconButton label={`Delete ${site.name}`} icon={Trash2} variant="ghost" size="sm" onClick={onRemove} />
      </span>
    </li>
  );
}

function LanesPanel({ fleet, draft, ends, onSetEnd, onStartDraw, onUndo, onFinish, onCloseLoop, onCancel, hoverLane, onHoverLane,
  nodeMode, onNodeMode, selectedNode, onDeleteNode, placing, onPlace, onCancelPlace, hoverSite, onHoverSite, commitLayout }) {
  const toast = useToast();
  const layout = fleet.layout;
  const [confirm, setConfirm] = useState(null);
  const [radius, setRadius] = useState(null);
  const lanes = layout?.lanes || [];
  const robots = fleet.state.robots.filter((r) => r.adopted);
  const r = radius ?? layout?.lane_corner_radius ?? 0.6;

  const put = (patch, title) => fleet.act((c) => c.putLayout({ ...layout, ...patch }))
    .then((res) => { fleet.setLayout(res.layout); if (title) toast({ title, body: layout?.sync ? "Sent to every robot in the fleet." : undefined }); })
    .catch((e) => toast({ title: "Layout not saved", body: e.message, tone: "error" }));

  const importFrom = (rb) => fleet.act((c) => c.importLayout(rb.key))
    .then((res) => { fleet.setLayout(res.layout); toast({ title: `Layout taken from ${rb.label}`, body: `${res.layout.lanes.length} lanes, ${res.layout.racks.length} racks, ${res.layout.drop_zones.length} zones.` }); })
    .catch((e) => toast({ title: "Couldn't import", body: e.message, tone: "error" }));

  const total = lanes.reduce((d, l) => d + length(l.points), 0);
  const graph = buildGraph(lanes);

  return (
    <div className="panel">
      <p className="hint">
        One lane network for the whole fleet. Robots route along it with rounded corners, and the laptop keeps every robot’s copy in step.
      </p>

      {draft ? (
        <div className="drawpanel">
          <div className="drawbar" role="status">
            <div className="drawbar__text">
              <b>Drawing a lane</b>
              <span className="num">{draft.points.length} point{draft.points.length === 1 ? "" : "s"}. Click the floor to add, double-click or <Kbd>Enter</Kbd> to finish.</span>
            </div>
            <div className="drawbar__actions">
              <IconButton label="Undo point" icon={Undo2} variant="ghost-inverse" size="sm" onClick={onUndo} disabled={!draft.points.length} />
              <IconButton label="Cancel" icon={X} variant="ghost-inverse" size="sm" onClick={onCancel} />
            </div>
          </div>
          {ends ? (
            <div className="drawends">
              <EndRow label="Start" end={ends.start} onChange={(v) => onSetEnd("start", v)} />
              <EndRow label="End" end={ends.end} onChange={(v) => onSetEnd("end", v)} />
            </div>
          ) : (
            <p className="hint">Place the first point. Each end can join the lanes it touches or stay open.</p>
          )}
          <div className="drawpanel__actions">
            <Button size="sm" variant="secondary" icon={Repeat} onClick={onCloseLoop} disabled={draft.points.length < 3}>Close loop</Button>
            <Button size="sm" variant="primary" icon={Check} onClick={onFinish} disabled={draft.points.length < 2}>Finish</Button>
          </div>
        </div>
      ) : (
        <div className="toolbar">
          <Button icon={Spline} variant="primary" onClick={() => { onNodeMode?.(false); onStartDraw(); }} disabled={!fleet.floor.grid}>Draw lane</Button>
          <Button icon={Waypoints} variant={nodeMode ? "primary" : "secondary"} aria-pressed={!!nodeMode}
            onClick={() => onNodeMode?.(!nodeMode)} disabled={!lanes.length}>{nodeMode ? "Done editing nodes" : "Edit nodes"}</Button>
          <Menu>
            <MenuTrigger asChild><Button icon={Download} variant="secondary" disabled={!robots.some((x) => x.linked)}>Take from robot</Button></MenuTrigger>
            <MenuContent>
              <MenuLabel>Replace the fleet layout with</MenuLabel>
              {robots.filter((x) => x.linked).map((x) => (
                <MenuItem key={x.key} onSelect={() => setConfirm({ kind: "import", robot: x })} hint="lanes, racks and zones">{x.label}</MenuItem>
              ))}
            </MenuContent>
          </Menu>
        </div>
      )}

      <section aria-labelledby="lanes-h">
        <div className="section-head">
          <h2 id="lanes-h">Lanes</h2>
          <span className="count num">{lanes.length} · {fmt(total, 1)} m</span>
        </div>
        {lanes.length ? (
          <ul className="sites">
            {lanes.map((l) => (
              <li key={l.id} className={cx("site", hoverLane === l.id && "is-hovered")}
                onMouseEnter={() => onHoverLane(l.id)} onMouseLeave={() => onHoverLane(null)}>
                <span className="glyph glyph--lane" />
                <span className="site__name site__name--static">{l.name || l.id}<span className="lane__shape">{laneShape(l, graph)}</span></span>
                <span className="site__xy num">{fmt(length(l.points), 1)} m</span>
                <span className="site__actions">
                  <IconButton label={l.oneway ? `${l.name || "Lane"} is one-way: make it two-way` : `Make ${l.name || "lane"} one-way`}
                    icon={MoveRight} variant={l.oneway ? "primary" : "ghost"} size="sm" aria-pressed={!!l.oneway}
                    onClick={() => put({ lanes: lanes.map((x) => (x.id === l.id ? { ...x, oneway: !x.oneway } : x)) },
                      l.oneway ? `${l.name || "Lane"} is two-way` : `${l.name || "Lane"} is one-way`)} />
                  {l.oneway && (
                    <IconButton label={`Reverse ${l.name || "lane"}'s direction`} icon={ArrowLeftRight} variant="ghost" size="sm"
                      onClick={() => put({ lanes: lanes.map((x) => (x.id === l.id ? { ...x, points: [...x.points].reverse() } : x)) },
                        `${l.name || "Lane"} reversed`)} />
                  )}
                  <IconButton label={`Delete ${l.name || "lane"}`} icon={Trash2} variant="ghost" size="sm"
                    onClick={() => put({ lanes: lanes.filter((x) => x.id !== l.id) }, `${l.name || "Lane"} deleted`)} />
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">No lanes yet. Robots plan freely with Nav2 until you draw lanes, or take a robot’s lanes as the fleet’s.</p>
        )}
      </section>

      {nodeMode && (
        <div className="nodebar" role="status">
          {selectedNode ? (
            <>
              <span className="num">Node at {fmt(selectedNode.x, 2)}, {fmt(selectedNode.y, 2)} · {selectedNode.deg === 1 ? "open end" : selectedNode.deg > 2 ? `junction of ${selectedNode.deg}` : "on a lane"}</span>
              <Button size="sm" variant="secondary" icon={Trash2} onClick={onDeleteNode}>Remove node</Button>
            </>
          ) : <span className="hint">Select a node on the floor. Gold rings are open ends; drag one onto another node to link them.</span>}
        </div>
      )}

      <section aria-labelledby="sites-h">
        <div className="section-head">
          <h2 id="sites-h">Racks and drop zones</h2>
          <span className="count num">{(layout?.racks?.length || 0) + (layout?.drop_zones?.length || 0)}</span>
        </div>
        <p className="hint">Each site’s arrow is its face: robots enter only from there, straight from that point on the lane.</p>
        {placing ? (
          <div className="toolbar"><Button size="sm" variant="ghost" icon={X} onClick={onCancelPlace}>Cancel placing</Button></div>
        ) : (
          <div className="toolbar">
            <Button size="sm" variant="secondary" icon={Plus} onClick={() => onPlace({ kind: "rack" })} disabled={!fleet.floor.grid}>Add rack</Button>
            <Button size="sm" variant="secondary" icon={Plus} onClick={() => onPlace({ kind: "zone" })} disabled={!fleet.floor.grid}>Add drop zone</Button>
          </div>
        )}
        <ul className="sites siterows">
          {[...(layout?.racks || []).map((s) => ["rack", s]), ...(layout?.drop_zones || []).map((s) => ["zone", s])].map(([kind, s]) => (
            <SiteRow key={s.id} site={s} kind={kind} lanes={lanes} hover={hoverSite === s.id} onHover={onHoverSite}
              onMove={() => onPlace({ move: s.id })}
              onRemove={() => {
                const key = kind === "rack" ? "racks" : "drop_zones";
                commitLayout({ [key]: layout[key].filter((x) => x.id !== s.id) }, `${s.name} removed`);
              }}
              onChange={(patchSite) => {
                const key = kind === "rack" ? "racks" : "drop_zones";
                commitLayout({ [key]: layout[key].map((x) => (x.id === s.id ? { ...x, ...patchSite } : x)) });
              }} />
          ))}
        </ul>
      </section>

      <section className="field">
        <span className="field__label">Corner radius <span className="field__value num">{fmt(r, 2)} m</span></span>
        <Slider value={r} min={0} max={1.2} step={0.05} onValueChange={setRadius}
          onValueCommit={(v) => { setRadius(null); put({ lane_corner_radius: v }); }} aria-label="Lane corner radius" />
        <span className="hint">Bends and junctions are followed as one curve of this radius.</span>
      </section>

      <section aria-labelledby="sync-h">
        <div className="section-head">
          <h2 id="sync-h">On the robots</h2>
          <span className="count num">rev {layout?.rev ?? 0}</span>
        </div>
        <label className="switchrow">
          <span>
            <span className="switchrow__title">Keep robots in sync</span>
            <span className="hint">Lanes, racks, zones and rack occupancy follow the fleet copy.</span>
          </span>
          <Switch.Root className="switch" checked={!!layout?.sync} onCheckedChange={(v) => put({ sync: v })} aria-label="Keep robots in sync">
            <Switch.Thumb className="switch__thumb" />
          </Switch.Root>
        </label>
        <ul className="syncrows">
          {robots.map((x) => (
            <li key={x.key}>
              <RobotGlyph robot={x} size={18} />
              <span>{x.label}</span>
              <span className={cx("syncstate", `is-${x.linked ? x.layout_sync : "off"}`)}>
                {!x.linked ? "Not linked" : x.layout_sync === "synced" ? <><Check size={13} strokeWidth={2.25} aria-hidden="true" /> In sync</>
                  : x.layout_sync === "sending" ? <><RefreshCw size={13} strokeWidth={2} aria-hidden="true" /> Sending</> : "Sync off"}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <ConfirmDialog
        open={confirm?.kind === "import"}
        onOpenChange={(o) => !o && setConfirm(null)}
        tone="primary"
        title={`Use ${confirm?.robot?.label}'s layout for the fleet?`}
        body="Its lanes, racks and drop zones replace the fleet layout, and every other robot receives them."
        confirmLabel="Replace layout"
        onConfirm={() => importFrom(confirm.robot)}
      />
    </div>
  );
}

export default memo(LanesPanel);
