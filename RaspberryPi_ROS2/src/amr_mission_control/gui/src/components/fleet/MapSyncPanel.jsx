import { useEffect, useRef, useState, memo } from "react";
import { Upload, Download, Trash2, MoreHorizontal, PencilLine } from "lucide-react";
import { Button, IconButton, Tip, Menu, MenuTrigger, MenuContent, MenuItem, ConfirmDialog } from "../ui";
import { gridFromPGM, knownBounds } from "../../lib/grid";
import { rasterize } from "../map/draw";
import { ago } from "../../lib/stackPrefs";
import { useToast } from "../../lib/toast";
import { cx } from "../../lib/cx";

function LibThumb({ client, info }) {
  const ref = useRef(null);
  useEffect(() => {
    let alive = true;
    client.pgm(info.name).then((buf) => {
      const c = ref.current;
      if (!alive || !c || !info.resolution) return;
      const g = gridFromPGM(buf, info);
      const ctx = c.getContext("2d");
      const b = knownBounds(g) || { x0: g.origin.x, y0: g.origin.y, x1: g.origin.x + g.width * g.resolution, y1: g.origin.y + g.height * g.resolution };
      const r = g.resolution;
      const sx = (b.x0 - g.origin.x) / r, sw = (b.x1 - b.x0) / r;
      const sy = g.height - (b.y1 - g.origin.y) / r, sh = (b.y1 - b.y0) / r;
      const k = Math.min(c.width / sw, c.height / sh);
      ctx.fillStyle = "#e8e9ec";
      ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(rasterize(g, "map"), sx, sy, sw, sh, (c.width - sw * k) / 2, (c.height - sh * k) / 2, sw * k, sh * k);
    }).catch(() => {});
    return () => { alive = false; };
  }, [client, info.name, info.fingerprint, info.resolution]); // eslint-disable-line react-hooks/exhaustive-deps
  return <canvas ref={ref} className="thumb" width="96" height="72" aria-hidden="true" />;
}

/** One cell of the library × robot matrix. */
function cellOf(lib, robotMaps, robot) {
  const m = robotMaps?.find((x) => x.name === lib.name);
  const running = robot.stack?.running && robot.stack.map === lib.name;
  if (!m) return { state: "missing", running, label: "Missing" };
  if (m.fingerprint === lib.fingerprint) return { state: "same", running, label: "Same copy" };
  return { state: "differs", running, label: `Different copy (${m.fingerprint})` };
}

function MapSyncPanel({ fleet, onEdit, editing }) {
  const toast = useToast();
  const [busy, setBusy] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const maps = fleet.maps;
  const robots = fleet.state.robots.filter((r) => r.adopted || (r.status === "online" && r.configured));
  if (!maps) return <div className="panel"><p className="empty">Loading the map library…</p></div>;

  const fail = (title) => (e) => toast({ title, body: e.message, tone: "error" });
  const push = async (lib, keys, overwrite) => {
    setBusy(`push:${lib.name}`);
    try {
      const res = await fleet.act((c) => c.push(lib.name, keys, overwrite));
      const ok = Object.entries(res.results).filter(([, v]) => v.ok).length;
      const bad = Object.entries(res.results).filter(([, v]) => !v.ok);
      const label = (k) => robots.find((r) => r.key === k)?.label || k;
      if (ok) toast({ title: `${lib.name} sent to ${ok} robot${ok > 1 ? "s" : ""}` });
      bad.forEach(([k, v]) => toast({ title: `${label(k)} kept its copy`, body: v.error, tone: "error" }));
      fleet.refreshMaps();
    } catch (e) { fail("Map not sent")(e); } finally { setBusy(null); }
  };
  const pull = async (robot, name, overwrite) => {
    setBusy(`pull:${robot.key}:${name}`);
    try {
      await fleet.act((c) => c.pull(robot.key, name, { overwrite }));
      toast({ title: `${name} copied from ${robot.label}`, body: "It is now the fleet's master copy." });
      fleet.refreshMaps();
    } catch (e) { fail("Map not copied")(e); } finally { setBusy(null); }
  };

  // Maps that live on robots but not (or differently) in the library.
  const lib = new Map(maps.library.map((m) => [m.name, m]));
  const offLibrary = [];
  robots.forEach((r) => (maps.robots[r.key] || []).forEach((m) => {
    const l = lib.get(m.name);
    if (!l || l.fingerprint !== m.fingerprint) offLibrary.push({ robot: r, map: m, inLibrary: !!l });
  }));

  return (
    <div className="panel">
      <p className="hint">
        Robots only share coordinates when they localize on the same pose graph, so the laptop keeps a master copy of each map and compares fingerprints.
      </p>

      <section aria-labelledby="lib-h">
        <div className="section-head"><h2 id="lib-h">Fleet library</h2><span className="count num">{maps.library.length}</span></div>
        {maps.library.length ? (
          <ul className="libmaps">
            {maps.library.map((m) => {
              const cells = robots.map((r) => ({ r, ...cellOf(m, maps.robots[r.key], r) }));
              const stale = cells.filter((c) => c.state !== "same");
              const sendable = stale.filter((c) => !c.running);
              return (
                <li key={m.name} className="libmap">
                  <LibThumb client={fleet.client} info={m} />
                  <div className="libmap__body">
                    <div className="libmap__title">
                      <span className="maprow__name">{m.name}</span>
                      {fleet.fleetMap === m.name && <span className="tag tag--live">Fleet floor</span>}
                      {!m.has_posegraph && <span className="tag tag--warn">No pose graph</span>}
                    </div>
                    <span className="maprow__meta num">{m.fingerprint}{m.modified ? ` · ${ago(m.modified)}` : ""}</span>
                    <ul className="cells" aria-label={`${m.name} on each robot`}>
                      {cells.map((c) => (
                        <li key={c.r.key}>
                          <Tip label={`${c.r.label}: ${c.label}${c.running ? ", running now" : ""}`}>
                            <button
                              type="button"
                              className={cx("cell", `cell--${c.state}`, c.running && "is-running")}
                              aria-label={`${c.r.label}: ${c.label}${c.running ? ", running now" : ""}${c.state !== "same" && !c.running ? ". Send the library copy" : ""}`}
                              disabled={c.state === "same" || c.running || !!busy}
                              onClick={() => (c.state === "differs" ? setConfirm({ kind: "push", lib: m, keys: [c.r.key] }) : push(m, [c.r.key], false))}
                            >
                              {c.r.label}
                            </button>
                          </Tip>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="libmap__actions">
                    <Tip label={sendable.length ? `Send to ${sendable.map((c) => c.r.label).join(", ")}` : stale.length ? "Robots with a different copy are running it; stop their stacks first" : "Every robot has this copy"}>
                      <span className="tipwrap">
                        <IconButton label={`Send ${m.name} to robots`} icon={Upload} variant="ghost" size="sm"
                          disabled={!sendable.length || !!busy}
                          onClick={() => (sendable.some((c) => c.state === "differs")
                            ? setConfirm({ kind: "push", lib: m, keys: sendable.map((c) => c.r.key) })
                            : push(m, sendable.map((c) => c.r.key), false))} />
                      </span>
                    </Tip>
                    <Menu>
                      <MenuTrigger asChild><IconButton label={`More for ${m.name}`} icon={MoreHorizontal} variant="ghost" size="sm" /></MenuTrigger>
                      <MenuContent align="end">
                        <MenuItem icon={PencilLine} disabled={!m.resolution || !!editing} onSelect={() => onEdit?.(m)}
                          hint="paint walls and free floor; robots get it live">Edit map</MenuItem>
                        <MenuItem icon={Trash2} tone="danger" onSelect={() => setConfirm({ kind: "delete", lib: m })}>Remove from library</MenuItem>
                      </MenuContent>
                    </Menu>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="empty">The library is empty. Copy a map from a robot below; it becomes the copy every robot gets.</p>
        )}
        <div className="legend legend--cells" aria-hidden="true">
          <span><i className="cell-key cell--same" />Same copy</span>
          <span><i className="cell-key cell--differs" />Different</span>
          <span><i className="cell-key cell--missing" />Missing</span>
          <span><i className="cell-key is-running" />Running</span>
        </div>
      </section>

      <section aria-labelledby="off-h">
        <div className="section-head"><h2 id="off-h">On robots only</h2><span className="count num">{offLibrary.length}</span></div>
        {offLibrary.length ? (
          <ul className="sites">
            {offLibrary.map(({ robot, map, inLibrary }) => (
              <li key={`${robot.key}:${map.name}`} className="site site--map">
                <span className={cx("glyph", inLibrary ? "glyph--occ glyph--zone" : "glyph--free")} />
                <span className="site__name site__name--static">{map.name} <span className="muted">on {robot.label}</span></span>
                <span className="site__xy num">{map.fingerprint}</span>
                <Button size="sm" variant="secondary" icon={Download} disabled={!!busy}
                  onClick={() => (inLibrary ? setConfirm({ kind: "pull", robot, name: map.name }) : pull(robot, map.name, false))}>
                  {inLibrary ? "Replace" : "Copy"}
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty empty--row">Every map on the robots matches the library.</p>
        )}
      </section>

      <ConfirmDialog
        open={confirm?.kind === "push"}
        onOpenChange={(o) => !o && setConfirm(null)}
        tone="primary"
        title={`Replace ${confirm?.lib?.name} on ${confirm?.keys?.length === 1 ? robots.find((r) => r.key === confirm.keys[0])?.label : `${confirm?.keys?.length} robots`}?`}
        body="Robots with a different copy get the library's image and pose graph instead. Their old copy is overwritten."
        confirmLabel="Replace"
        onConfirm={() => push(confirm.lib, confirm.keys, true)}
      />
      <ConfirmDialog
        open={confirm?.kind === "pull"}
        onOpenChange={(o) => !o && setConfirm(null)}
        tone="primary"
        title={`Make ${confirm?.robot?.label}'s ${confirm?.name} the master copy?`}
        body="The library copy is replaced. Robots with the old copy then show as different until you send them this one."
        confirmLabel="Replace library copy"
        onConfirm={() => pull(confirm.robot, confirm.name, true)}
      />
      <ConfirmDialog
        open={confirm?.kind === "delete"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Remove ${confirm?.lib?.name} from the library?`}
        body="Robots keep their own copies. You can copy it back from any robot that has it."
        confirmLabel="Remove"
        onConfirm={() => fleet.act((c) => c.removeMap(confirm.lib.name)).then(fleet.refreshMaps)}
      />
    </div>
  );
}

export default memo(MapSyncPanel);
