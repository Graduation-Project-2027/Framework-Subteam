/** HTTP client for launcher_server.py (stack control + map library). */
export const LAUNCHER_URL = `http://${window.location.hostname || "localhost"}:8088`;

async function callAt(base, path, { method = "GET", body, raw = false } = {}) {
  let res;
  try {
    res = await fetch(`${base}${path}`, {
      method,
      body,
      cache: "no-store",
      headers: body ? { "Content-Type": "application/octet-stream" } : undefined,
    });
  } catch {
    const err = new Error("The launcher isn't reachable. Start launcher_server.py on the robot.");
    err.offline = true;
    throw err;
  }
  if (raw && res.ok) return res.arrayBuffer();
  let json = {};
  try { json = await res.json(); } catch { /* empty body */ }
  if (!res.ok || json.ok === false) throw new Error(json.error || `Launcher error ${res.status}`);
  return json;
}

const enc = encodeURIComponent;

export function createHttpStack(base = LAUNCHER_URL) {
  const call = (path, opts) => callAt(base, path, opts);
  return {
    kind: "http",
    status: () => call("/api/status"),
    logs: (after) => call(`/api/logs?after=${after || 0}`),
    launch: ({ target, mode, map, rviz }) =>
      call(`/api/launch?target=${target}&mode=${mode}${map ? `&map=${enc(map)}` : ""}&rviz=${rviz ? 1 : 0}`, { method: "POST" }),
    stop: () => call("/api/stop", { method: "POST" }),
    rviz: (on) => call(`/api/rviz?on=${on ? 1 : 0}`, { method: "POST" }),
    maps: () => call("/api/maps"),
    pgm: (name) => call(`/api/maps/${enc(name)}/pgm`, { raw: true }),
    putPgm: (name, bytes) => call(`/api/maps/${enc(name)}/pgm`, { method: "PUT", body: bytes }),
    saveMap: (name, overwrite) => call(`/api/maps/save?name=${enc(name)}${overwrite ? "&overwrite=1" : ""}`, { method: "POST" }),
    rename: (name, to) => call(`/api/maps/${enc(name)}/rename?to=${enc(to)}`, { method: "POST" }),
    duplicate: (name, to) => call(`/api/maps/${enc(name)}/duplicate?to=${enc(to)}`, { method: "POST" }),
    setDefault: (name) => call(`/api/maps/${enc(name)}/default`, { method: "POST" }),
    revert: (name) => call(`/api/maps/${enc(name)}/revert`, { method: "POST" }),
    remove: (name) => call(`/api/maps/${enc(name)}/delete`, { method: "POST" }),
    restore: (id) => call(`/api/maps/restore?id=${enc(id)}`, { method: "POST" }),
  };
}

export const MAP_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/;
export const MAP_NAME_HINT = "Letters, digits, - and _ only (up to 40).";
