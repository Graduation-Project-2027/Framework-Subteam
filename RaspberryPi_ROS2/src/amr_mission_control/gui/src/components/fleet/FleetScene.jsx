/**
 * The fleet floor in 3D: a white architectural model of the site on drafting
 * paper. Walls are low plaster extrusions of the map, racks are ink frames,
 * lanes are gold floor tape, and every robot is its black chassis with the
 * E-JUST red disc. Reserved floor (each driving robot's claim) is a faint
 * ink strip; claims in conflict turn gold, like the hold they cause.
 */
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, Html, Line, RoundedBox, PerspectiveCamera, OrthographicCamera } from "@react-three/drei";
import * as THREE from "three";
import { laneGeometry, DEFAULT_RADIUS, snapToNode } from "../../lib/lanes";
import { RACK_SIDE } from "../../lib/geometry";
import { isDriving, radiusOf, approachFoot } from "../../lib/traffic";
import { stateMeta } from "../../lib/mission";
import { robotState } from "../../lib/fleetStatus";
import { ribbon, contentBounds, floorTexture, wallRuns } from "./geometry3d";

const COL = {
  ink: "#141416", ink2: "#2e3035", paper: "#ffffff", plaster: "#d9dbe0", red: "#e3202a",
  gold: "#de9242", goldInk: "#8a5410", ground: "#eceef1",
};
const WALL_H = 0.42;
const WALL_MIN = 0.4; // shorter marks (rack legs, speckle) stay printed on the floor
const RACK_H = 0.72;
const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ── Site floor ─────────────────────────────────────────────────────── */
function Floor({ grid, ver, bounds, onFloor, drawing, painting, dragging, placing, shadows }) {
  // ver: the grid's edit version, so painting reprints the floor.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const tex = useMemo(() => (grid ? floorTexture(grid, bounds) : null), [grid, bounds, ver]);
  useEffect(() => () => tex?.texture.dispose(), [tex]);
  const b = tex || bounds;
  const w = b.x1 - b.x0, h = b.y1 - b.y0;
  const handler = (kind) => (e) => {
    if (!onFloor) return;
    if (kind === "click" && e.delta > 4) return; // an orbit drag, not a click
    e.stopPropagation();
    onFloor(kind, e.point.x, e.point.y);
  };
  return (
    <>
      <mesh position={[(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, -0.02]}>
        <planeGeometry args={[w + 400, h + 400]} />
        <meshBasicMaterial color={COL.ground} />
      </mesh>
      {/* Unlit paper: the plan prints at its true colours; shadows land on a catcher above it. */}
      <mesh
        position={[(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, 0]}
        onClick={painting || dragging ? undefined : handler("click")}
        onPointerMove={drawing || painting || dragging || placing ? handler("move") : undefined}
        onDoubleClick={drawing ? handler("double") : undefined}
        onPointerDown={painting ? (e) => { if (e.button !== 0) return; e.target.setPointerCapture?.(e.pointerId); handler("down")(e); } : undefined}
        onPointerUp={painting || dragging ? (e) => { e.target.releasePointerCapture?.(e.pointerId); handler("up")(e); } : undefined}
      >
        <planeGeometry args={[w, h]} />
        {/* Keyed on the texture: a material compiled without a map never shows one added later. */}
        <meshBasicMaterial key={tex ? tex.texture.uuid : "plain"} map={tex?.texture || null} color="#ffffff" toneMapped={false} />
      </mesh>
      <mesh position={[(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, 0.001]} receiveShadow raycast={() => null}>
        <planeGeometry args={[w, h]} />
        <shadowMaterial transparent opacity={shadows ? 0.14 : 0} />
      </mesh>
    </>
  );
}

function Walls({ grid, ver, bounds }) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const runs = useMemo(() => (grid ? wallRuns(grid, bounds).filter((r) => r[2] >= WALL_MIN) : []), [grid, bounds, ver]);
  const ref = useRef(null);
  useLayoutEffect(() => {
    const m = ref.current;
    if (!m) return;
    const o = new THREE.Object3D();
    runs.forEach(([x, y, len], i) => {
      o.position.set(x, y, WALL_H / 2);
      o.scale.set(len, grid.resolution, WALL_H);
      o.updateMatrix();
      m.setMatrixAt(i, o.matrix);
    });
    m.instanceMatrix.needsUpdate = true;
    m.computeBoundingSphere();
  }, [runs, grid]);
  if (!runs.length) return null;
  return (
    <instancedMesh ref={ref} args={[null, null, runs.length]} castShadow receiveShadow>
      <boxGeometry args={[1, 1, 1]} />
      <meshStandardMaterial color={COL.plaster} roughness={0.9} />
    </instancedMesh>
  );
}

/* ── Racks and zones ────────────────────────────────────────────────── */
const LEG = 0.05;
function RackModel({ lift = 0 }) {
  const h = RACK_SIDE / 2 - LEG / 2;
  return (
    <group position={[0, 0, lift]}>
      {[[h, h], [h, -h], [-h, h], [-h, -h]].map(([x, y]) => (
        <mesh key={`${x},${y}`} position={[x, y, RACK_H / 2]} castShadow>
          <boxGeometry args={[LEG, LEG, RACK_H]} />
          <meshStandardMaterial color={COL.ink2} roughness={0.6} />
        </mesh>
      ))}
      <mesh position={[0, 0, RACK_H + 0.015]} castShadow receiveShadow>
        <boxGeometry args={[RACK_SIDE, RACK_SIDE, 0.03]} />
        <meshStandardMaterial color={COL.ink} roughness={0.55} />
      </mesh>
    </group>
  );
}

const SQUARE = (s) => [[-s, -s, 0], [s, -s, 0], [s, s, 0], [-s, s, 0], [-s, -s, 0]];

/** The site's face: an arrow from its front edge to the exact point on the
 *  lane the robot enters from (straight in, at 90° to the lane). */
function Face({ site, lanes, highlight }) {
  const ax = -Math.cos(site.yaw || 0), ay = -Math.sin(site.yaw || 0);
  const entry = lanes?.length ? approachFoot(lanes, site) : null;
  const edge = RACK_SIDE / 2 + 0.04;
  const sx = site.x + ax * edge, sy = site.y + ay * edge;
  const ex = entry ? entry.x : site.x + ax * (edge + 0.8), ey = entry ? entry.y : site.y + ay * (edge + 0.8);
  const L = Math.hypot(ex - sx, ey - sy);
  const head = Math.min(0.22, L * 0.4);
  const bx = ex - ax * head, by = ey - ay * head;
  const tri = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute([
      ex, ey, 0.02, bx - ay * 0.1, by + ax * 0.1, 0.02, bx + ay * 0.1, by - ax * 0.1, 0.02], 3));
    return g;
  }, [ex, ey, bx, by, ax, ay]);
  useEffect(() => () => tri.dispose(), [tri]);
  if (L < 0.05) return null;
  const color = highlight ? COL.red : COL.ink;
  return (
    <group>
      <Line points={[[sx, sy, 0.02], [bx, by, 0.02]]} color={color} lineWidth={highlight ? 2.2 : 1.4}
        transparent opacity={highlight ? 1 : 0.7} />
      <mesh geometry={tri}>
        <meshBasicMaterial color={color} side={THREE.DoubleSide} transparent opacity={highlight ? 1 : 0.8} />
      </mesh>
      {entry && (
        <mesh position={[entry.x, entry.y, 0.021]}>
          <ringGeometry args={[0.07, 0.11, 24]} />
          <meshBasicMaterial color={color} />
        </mesh>
      )}
    </group>
  );
}

function Site({ site, kind, carried, labels }) {
  const occupied = (site.state || (kind === "zone" ? "free" : "occupied")) === "occupied";
  return (
    <group position={[site.x, site.y, 0]} rotation={[0, 0, site.yaw || 0]}>
      {occupied && !carried ? (
        <RackModel />
      ) : (
        <Line points={SQUARE(RACK_SIDE / 2)} position={[0, 0, 0.006]} color={COL.ink} lineWidth={1.25}
          dashed={kind === "zone"} dashSize={0.12} gapSize={0.08} />
      )}
      {labels && (
        <Html position={[0, 0, occupied && !carried ? RACK_H + 0.1 : 0.05]} center zIndexRange={[20, 0]} className="r3site">
          <span className={occupied && !carried ? "is-occ" : ""}>{site.name}</span>
        </Html>
      )}
    </group>
  );
}

/* ── Lanes ──────────────────────────────────────────────────────────── */
function Lanes({ lanes, radius, hoverId }) {
  const geo = useMemo(() => {
    const { nodes, adj, edges, arcs } = laneGeometry(lanes, radius);
    const pieces = [...edges, ...arcs].map((p) => ribbon(p, 0.09, 0.004));
    const junctions = nodes.filter((_, i) => adj[i].length > 2);
    // Open ends get a stop bar across the tape, like the end of a floor marking.
    const caps = nodes.flatMap((n, i) => {
      if (adj[i].length !== 1) return [];
      const m = nodes[adj[i][0].j];
      const d = Math.hypot(m.x - n.x, m.y - n.y) || 1;
      const nx = -(m.y - n.y) / d, ny = (m.x - n.x) / d;
      return [ribbon([{ x: n.x - nx * 0.2, y: n.y - ny * 0.2 }, { x: n.x + nx * 0.2, y: n.y + ny * 0.2 }], 0.06, 0.005)];
    });
    // One-way lanes: a dark chevron every 1.4 m, pointing the way to drive.
    const tri = [];
    for (const l of lanes) {
      if (!l.oneway) continue;
      for (let i = 0; i < l.points.length - 1; i++) {
        const a = l.points[i], b = l.points[i + 1];
        const L = Math.hypot(b.x - a.x, b.y - a.y);
        const ux = (b.x - a.x) / L, uy = (b.y - a.y) / L;
        for (let t = 0.9; t < L - 0.9; t += 1.4) {
          const cx = a.x + ux * t, cy = a.y + uy * t;
          tri.push(cx + ux * 0.16, cy + uy * 0.16, 0.007,
            cx - ux * 0.1 - uy * 0.11, cy - uy * 0.1 + ux * 0.11, 0.007,
            cx - ux * 0.1 + uy * 0.11, cy - uy * 0.1 - ux * 0.11, 0.007);
        }
      }
    }
    let arrows = null;
    if (tri.length) {
      arrows = new THREE.BufferGeometry();
      arrows.setAttribute("position", new THREE.Float32BufferAttribute(tri, 3));
    }
    return { pieces, junctions, caps, arrows };
  }, [lanes, radius]);
  useEffect(() => () => [...geo.pieces, ...geo.caps, geo.arrows].forEach((g) => g?.dispose()), [geo]);
  const hovered = lanes.find((l) => l.id === hoverId);
  return (
    <group>
      {geo.pieces.map((g, i) => (
        <mesh key={i} geometry={g} receiveShadow>
          <meshStandardMaterial color={COL.gold} roughness={0.8} />
        </mesh>
      ))}
      {geo.arrows && (
        <mesh geometry={geo.arrows}>
          <meshBasicMaterial color={COL.goldInk} side={THREE.DoubleSide} />
        </mesh>
      )}
      {geo.caps.map((g, i) => (
        <mesh key={`e${i}`} geometry={g}>
          <meshBasicMaterial color={COL.goldInk} />
        </mesh>
      ))}
      {geo.junctions.map((n, i) => (
        <mesh key={`j${i}`} position={[n.x, n.y, 0.006]}>
          <circleGeometry args={[0.07, 24]} />
          <meshBasicMaterial color={COL.paper} />
        </mesh>
      ))}
      {hovered && (
        <Line points={hovered.points.map((p) => [p.x, p.y, 0.01])} color={COL.goldInk} lineWidth={4} />
      )}
    </group>
  );
}

/** Where a drawn end goes: a ring on the lane it joins, or a stop bar if open. */
function DraftEnd({ end, toward }) {
  if (!end) return null;
  if (end.joins) {
    return (
      <mesh position={[end.target.x, end.target.y, 0.016]}>
        <ringGeometry args={[0.12, 0.18, 32]} />
        <meshBasicMaterial color={COL.goldInk} />
      </mesh>
    );
  }
  const p = end.point;
  const d = toward ? Math.hypot(toward.x - p.x, toward.y - p.y) : 0;
  if (!d) return null;
  const nx = -(toward.y - p.y) / d, ny = (toward.x - p.x) / d;
  return (
    <Line points={[[p.x - nx * 0.2, p.y - ny * 0.2, 0.016], [p.x + nx * 0.2, p.y + ny * 0.2, 0.016]]} color={COL.goldInk} lineWidth={4} />
  );
}

/** Lane nodes as handles: drag to move, click to select. Open ends gold. */
function NodeHandles({ nodes, deg, selected, onDown, onUp }) {
  return (
    <group>
      {nodes.map((n, i) => (
        <group key={i} position={[n.x, n.y, 0.03]}>
          <mesh
            onPointerDown={(e) => { if (e.button !== 0) return; e.stopPropagation(); onDown(i); }}
            onPointerUp={(e) => { e.stopPropagation(); onUp(); }}
            onPointerOver={() => { document.body.style.cursor = "grab"; }}
            onPointerOut={() => { document.body.style.cursor = ""; }}
          >
            <circleGeometry args={[0.2, 28]} />
            <meshBasicMaterial color={i === selected ? COL.ink : COL.paper} />
          </mesh>
          <mesh position={[0, 0, 0.001]} raycast={() => null}>
            <ringGeometry args={[0.16, 0.2, 28]} />
            <meshBasicMaterial color={deg[i] === 1 ? COL.gold : i === selected ? COL.paper : COL.ink} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

function LaneDraft({ draft, cursor, ends, snap }) {
  if (!draft) return null;
  const pts = draft.points.map((p) => [p.x, p.y, 0.012]);
  const all = cursor && pts.length ? [...pts, [cursor.x, cursor.y, 0.012]] : pts;
  return (
    <group>
      {all.length >= 2 && <Line points={all} color={COL.goldInk} lineWidth={2.5} dashed dashSize={0.18} gapSize={0.1} />}
      {draft.points.map((p, i) => (
        <mesh key={i} position={[p.x, p.y, 0.014]}>
          <circleGeometry args={[0.08, 20]} />
          <meshBasicMaterial color={i === 0 ? COL.goldInk : COL.gold} />
        </mesh>
      ))}
      {snap && (
        <mesh position={[snap.x, snap.y, 0.018]}>
          <ringGeometry args={[0.2, 0.26, 32]} />
          <meshBasicMaterial color={snap.close ? COL.red : COL.goldInk} />
        </mesh>
      )}
      {ends?.start && draft.points.length > 1 && <DraftEnd end={ends.start} toward={draft.points[1]} />}
      {ends?.end && <DraftEnd end={ends.end} toward={draft.points[draft.points.length - 2]} />}
    </group>
  );
}

/* ── Robots ─────────────────────────────────────────────────────────── */
function Halo() {
  const ref = useRef(null);
  const still = useMemo(reduced, []);
  useFrame(({ clock }) => {
    if (!ref.current || still) return;
    const t = (clock.elapsedTime % 1.6) / 1.6;
    ref.current.scale.setScalar(1 + t * 0.9);
    ref.current.material.opacity = 0.5 * (1 - t);
  });
  return (
    <mesh ref={ref} position={[0, 0, 0.008]}>
      <ringGeometry args={[0.46, 0.5, 48]} />
      <meshBasicMaterial color={COL.red} transparent opacity={still ? 0.35 : 0.5} depthWrite={false} />
    </mesh>
  );
}

function RobotModel({ robot, selected, onSelect, poses }) {
  const ref = useRef(null);
  const m = robot.mission || {};
  const pose = m.pose || { x: 0, y: 0, yaw: 0 };
  const driving = isDriving(m) && !robot.hold;
  const held = !!robot.hold;
  const meta = stateMeta(m.state);
  const [hover, setHover] = useState(false);

  // Smooth towards the latest pose between polls.
  useFrame((_, dt) => {
    const g = ref.current;
    if (!g) return;
    const k = 1 - Math.exp(-dt * 9);
    if (!g.userData.init) {
      g.position.set(pose.x, pose.y, 0);
      g.rotation.z = pose.yaw;
      g.userData.init = true;
    }
    g.position.x += (pose.x - g.position.x) * k;
    g.position.y += (pose.y - g.position.y) * k;
    const dy = Math.atan2(Math.sin(pose.yaw - g.rotation.z), Math.cos(pose.yaw - g.rotation.z));
    g.rotation.z += dy * k;
    poses.current[robot.key] = { x: g.position.x, y: g.position.y };
  });
  useEffect(() => () => { document.body.style.cursor = ""; }, []);

  return (
    <group
      ref={ref}
      onClick={(e) => { if (e.delta > 4) return; e.stopPropagation(); onSelect(robot.key); }}
      onPointerOver={(e) => { e.stopPropagation(); setHover(true); document.body.style.cursor = "pointer"; }}
      onPointerOut={() => { setHover(false); document.body.style.cursor = ""; }}
    >
      <RoundedBox args={[0.7, 0.64, 0.2]} radius={0.035} smoothness={3} position={[0, 0, 0.13]} castShadow receiveShadow>
        <meshStandardMaterial color={COL.ink} roughness={0.45} metalness={0.1} />
      </RoundedBox>
      <mesh position={[0, 0, 0.232]} castShadow>
        <boxGeometry args={[0.46, 0.46, 0.006]} />
        <meshStandardMaterial color={COL.ink2} roughness={0.7} />
      </mesh>
      <mesh position={[0, 0, 0.237]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.1, 0.1, 0.006, 40]} />
        <meshStandardMaterial color={COL.red} roughness={0.5} emissive={COL.red} emissiveIntensity={0.15} />
      </mesh>
      <mesh position={[0.322, 0, 0.234]}>
        <boxGeometry args={[0.022, 0.38, 0.004]} />
        <meshBasicMaterial color={COL.paper} />
      </mesh>
      {m.carrying && <RackModel lift={0.05} />}
      {driving && <Halo />}
      {held && (
        <mesh position={[0, 0, 0.009]}>
          <ringGeometry args={[radiusOf(m) + 0.06, radiusOf(m) + 0.12, 56]} />
          <meshBasicMaterial color={COL.gold} transparent opacity={0.9} depthWrite={false} />
        </mesh>
      )}
      {(selected || hover) && (
        <mesh position={[0, 0, 0.01]}>
          <ringGeometry args={[radiusOf(m) + 0.16, radiusOf(m) + 0.19, 64]} />
          <meshBasicMaterial color={COL.ink} transparent opacity={selected ? 0.9 : 0.4} depthWrite={false} />
        </mesh>
      )}
      <Html position={[0, 0, (m.carrying ? RACK_H + 0.08 : 0.25) + 0.45]} center zIndexRange={[40, 20]} className="r3tag-wrap">
        <button
          type="button"
          className={`r3tag${selected ? " is-selected" : ""}${held ? " is-held" : ""}${meta.tone === "fault" ? " is-fault" : ""}`}
          onClick={() => onSelect(robot.key)}
        >
          <b>{robot.label}</b>
          {(held || selected || meta.tone !== "idle" || !robot.linked) && <span>{held ? `Holding for ${robot.hold.by_label}` : robotState(robot)}</span>}
        </button>
      </Html>
    </group>
  );
}

function Claim({ robot, conflicted }) {
  const geo = useMemo(() => (robot.claim?.length >= 2 ? ribbon(robot.claim, radiusOf(robot.mission) * 2, 0.003) : null), [robot.claim, robot.mission]);
  useEffect(() => () => geo?.dispose(), [geo]);
  if (!geo) return null;
  return (
    <mesh geometry={geo}>
      <meshBasicMaterial color={conflicted ? COL.gold : COL.ink} transparent opacity={conflicted ? 0.3 : 0.07} depthWrite={false} />
    </mesh>
  );
}

/** A robot backing off: where it reverses to along the lane (and its refuge). */
function Retreat({ robot }) {
  const rt = robot.hold?.retreat;
  if (!rt?.path?.length) return null;
  const pts = rt.path.map((p) => [p.x, p.y, 0.02]);
  if (rt.into) pts.push([rt.into.x, rt.into.y, 0.02]);
  return (
    <group>
      {pts.length >= 2 && <Line points={pts} color={COL.goldInk} lineWidth={2.5} dashed dashSize={0.12} gapSize={0.08} />}
      {rt.into && (
        <mesh position={[rt.into.x, rt.into.y, 0.022]}>
          <ringGeometry args={[0.3, 0.36, 40]} />
          <meshBasicMaterial color={COL.goldInk} />
        </mesh>
      )}
    </group>
  );
}

function Path({ robot, selected }) {
  if (robot.hold?.retreat) return null;
  const pts = robot.plan || [];
  if (pts.length < 2) return null;
  return (
    <Line points={pts.map((p) => [p.x, p.y, 0.014])} color={COL.ink} lineWidth={selected ? 1.75 : 1}
      transparent opacity={selected ? 0.85 : 0.4} dashed dashSize={0.14} gapSize={0.09} />
  );
}

/* ── Camera ─────────────────────────────────────────────────────────── */
function Rig({ view, bounds, focus, follow, poses }) {
  const { camera, size } = useThree();
  const ctl = useThree((s) => s.controls);
  const cx = (bounds.x0 + bounds.x1) / 2, cy = (bounds.y0 + bounds.y1) / 2;
  const span = Math.max(bounds.x1 - bounds.x0, bounds.y1 - bounds.y0);
  const fly = useRef(null);

  // Frame the site whenever the view or the site changes.
  useEffect(() => {
    const c = ctl;
    if (!c) return;
    // Aim a little towards the near edge so the foreground isn't lost under the legend.
    const ty = view === "top" ? cy : cy - span * 0.06;
    c.target.set(cx, ty, 0);
    if (view === "top") {
      camera.position.set(cx, cy, 60);
      camera.zoom = Math.min(size.width / (bounds.x1 - bounds.x0), size.height / (bounds.y1 - bounds.y0)) * 0.92;
    } else {
      camera.position.set(cx - span * 0.32, ty - span * 0.72, span * 0.58);
    }
    camera.updateProjectionMatrix();
    c.update();
    // Re-frame only on a view or site change, not on every resize.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, cx, cy, span, ctl]);

  useEffect(() => {
    if (focus) fly.current = { key: focus.key, t: 0 };
  }, [focus]);

  useFrame((_, dt) => {
    const c = ctl;
    if (!c) return;
    const target = fly.current ? poses.current[fly.current.key] : follow ? poses.current[follow] : null;
    if (!target) return;
    const k = fly.current ? 1 - Math.exp(-dt * 5) : 1 - Math.exp(-dt * 8);
    const dx = (target.x - c.target.x) * k, dy = (target.y - c.target.y) * k;
    c.target.x += dx;
    c.target.y += dy;
    camera.position.x += dx;
    camera.position.y += dy;
    if (fly.current) {
      fly.current.t += dt;
      if (fly.current.t > 1.2) fly.current = null;
    }
    c.update();
  });
  return null;
}

/* ── Scene ──────────────────────────────────────────────────────────── */
function FleetScene({
  grid, layout, robots, conflicts, selected, onSelect, view, follow, focus,
  laneDraft, laneEnds, onLanePoint, onLaneFinish, hoverLane, layers, editor, onPaint,
  nodeEdit, onNodeDown, onNodeDrag, placing, onPlace, snapNodes, faceFocus,
}) {
  const fleet = robots.filter((r) => r.adopted && r.mission?.pose);
  const bounds = useMemo(
    () => contentBounds(grid, layout, fleet),
    // Frame from the layout, the floor and where the robots are when they join.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [grid, layout?.rev, layout?.racks?.length, layout?.lanes?.length, fleet.length],
  );
  const poses = useRef({});
  const [cursor, setCursor] = useState(null);
  const carried = new Set(fleet.filter((r) => r.mission?.carrying && r.mission?.task?.rack_id).map((r) => r.mission.task.rack_id));
  const yielding = new Set(conflicts.filter((c) => c.kind === "yield").flatMap((c) => [c.a, c.b]));
  const drawing = !!laneDraft;
  const painting = !!editor;
  const dragging = !!nodeEdit?.dragging;
  const [brushAt, setBrushAt] = useState(null);
  const span = Math.max(bounds.x1 - bounds.x0, bounds.y1 - bounds.y0);

  const onFloor = (kind, x, y) => {
    if (dragging) { onNodeDrag(kind, x, y); return; }
    if (placing) {
      if (kind === "move") setCursor({ x, y });
      else if (kind === "click") onPlace(x, y);
      return;
    }
    if (painting) {
      if (kind === "move") setBrushAt({ x, y });
      onPaint(kind, x, y);
      return;
    }
    if (!drawing) return;
    if (kind === "move") setCursor({ x, y });
    else if (kind === "click") onLanePoint(x, y);
    else if (kind === "double") onLaneFinish();
  };

  return (
    <Canvas
      shadows
      flat
      dpr={[1, 2]}
      gl={{ antialias: true, preserveDrawingBuffer: false }}
      onPointerMissed={() => !drawing && !painting && !nodeEdit && !placing && onSelect(null)}
      className="scene3d__canvas"
      aria-label="3D view of the fleet floor"
    >
      {view === "top" ? (
        <OrthographicCamera makeDefault up={[0, 0, 1]} near={0.1} far={400} position={[0, 0, 60]} zoom={20} />
      ) : (
        <PerspectiveCamera makeDefault up={[0, 0, 1]} fov={34} near={0.1} far={span * 12 + 100} position={[0, -20, 15]} />
      )}
      <OrbitControls
        makeDefault
        enableDamping
        dampingFactor={0.12}
        enableRotate={view !== "top"}
        screenSpacePanning
        maxPolarAngle={Math.PI * 0.46}
        minDistance={2}
        maxDistance={span * 3 + 20}
        mouseButtons={drawing || painting || nodeEdit ? { LEFT: undefined, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN } : undefined}
      />
      <Rig view={view} bounds={bounds} focus={focus} follow={follow} poses={poses} />

      <ambientLight intensity={1.05} />
      <directionalLight
        position={[bounds.x0 - span * 0.3, bounds.y0 - span * 0.5, span * 0.9]}
        intensity={1.15}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0004}
        shadow-camera-left={-span}
        shadow-camera-right={span}
        shadow-camera-top={span}
        shadow-camera-bottom={-span}
        shadow-camera-near={0.5}
        shadow-camera-far={span * 4}
        target-position={[(bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2, 0]}
      />

      <Floor grid={grid} ver={grid?.version} bounds={bounds} onFloor={onFloor} drawing={drawing} painting={painting}
        dragging={dragging} placing={placing} shadows={view !== "top"} />
      <Walls grid={grid} ver={grid?.version} bounds={bounds} />
      {painting && brushAt && editor.tool === "brush" && (
        <mesh position={[brushAt.x, brushAt.y, 0.03]}>
          <ringGeometry args={[Math.max(0.01, editor.size / 2 - 0.02), editor.size / 2, 48]} />
          <meshBasicMaterial color={editor.value === "wall" ? COL.ink : COL.goldInk} />
        </mesh>
      )}
      {layers.lanes && layout?.lanes?.length > 0 && (
        <Lanes lanes={layout.lanes} radius={layout.lane_corner_radius ?? DEFAULT_RADIUS} hoverId={hoverLane} />
      )}
      {(layout?.racks || []).map((s) => <Site key={s.id} site={s} kind="rack" carried={carried.has(s.id)} labels={layers.labels} />)}
      {layers.labels && [...(layout?.racks || []), ...(layout?.drop_zones || [])].map((s) => (
        <Face key={`f${s.id}`} site={s} lanes={layout?.lanes} highlight={faceFocus === s.id} />
      ))}
      {(layout?.drop_zones || []).map((s) => <Site key={s.id} site={s} kind="zone" carried={false} labels={layers.labels} />)}
      {layers.claims && fleet.map((r) => <Claim key={`c${r.key}`} robot={r} conflicted={yielding.has(r.key)} />)}
      {layers.paths && fleet.map((r) => <Path key={`p${r.key}`} robot={r} selected={r.key === selected} />)}
      {fleet.map((r) => <Retreat key={`b${r.key}`} robot={r} />)}
      {fleet.map((r) => (
        <RobotModel key={r.key} robot={r} selected={r.key === selected} onSelect={onSelect} poses={poses} />
      ))}
      <LaneDraft draft={laneDraft} cursor={cursor} ends={laneEnds}
        snap={drawing && cursor ? snapToNode(cursor, snapNodes, laneDraft) : null} />
      {nodeEdit && <NodeHandles nodes={nodeEdit.nodes} deg={nodeEdit.deg} selected={nodeEdit.selected} onDown={onNodeDown} onUp={() => onNodeDrag("up")} />}
      {placing && cursor && (
        <mesh position={[cursor.x, cursor.y, 0.02]}>
          <ringGeometry args={[RACK_SIDE / 2 - 0.03, RACK_SIDE / 2, 4, 1, Math.PI / 4]} />
          <meshBasicMaterial color={COL.ink} />
        </mesh>
      )}
    </Canvas>
  );
}

export default memo(FleetScene);
