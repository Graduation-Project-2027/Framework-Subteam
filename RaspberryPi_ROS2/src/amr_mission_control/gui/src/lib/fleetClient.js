/** HTTP client for fleet_server.py (the laptop's fleet manager). */
const params = new URLSearchParams(window.location.search);
export const FLEET_URL = params.get("fleet") || `http://${window.location.hostname || "localhost"}:8090`;

async function call(path, { method = "GET", body, raw = false, bytes } = {}) {
  let res;
  try {
    res = await fetch(`${FLEET_URL}/api/fleet${path}`, {
      method,
      body: bytes ?? (body === undefined ? undefined : JSON.stringify(body)),
      cache: "no-store",
      headers: bytes ? { "Content-Type": "application/octet-stream" }
        : body === undefined ? undefined : { "Content-Type": "application/json" },
    });
  } catch {
    const err = new Error("The fleet manager isn't reachable. Start fleet_server.py on the laptop.");
    err.offline = true;
    throw err;
  }
  if (raw && res.ok) return res.arrayBuffer();
  let json = {};
  try { json = await res.json(); } catch { /* empty body */ }
  if (!res.ok || json.ok === false) throw new Error(json.error || `Fleet manager error ${res.status}`);
  return json;
}

const enc = encodeURIComponent;

export function createHttpFleet() {
  return {
    kind: "http",
    url: FLEET_URL,
    state: () => call("/state"),
    getLayout: () => call("/layout"),
    putLayout: (layout) => call("/layout", { method: "PUT", body: layout }),
    importLayout: (key) => call(`/layout/import?robot=${enc(key)}`, { method: "POST" }),
    maps: () => call("/maps"),
    pgm: (name) => call(`/maps/${enc(name)}/pgm`, { raw: true }),
    pull: (key, name, { as, overwrite } = {}) =>
      call(`/maps/pull?robot=${enc(key)}&name=${enc(name)}${as ? `&as=${enc(as)}` : ""}${overwrite ? "&overwrite=1" : ""}`, { method: "POST" }),
    push: (name, keys, overwrite) =>
      call(`/maps/push?name=${enc(name)}&robots=${keys.map(enc).join(",")}${overwrite ? "&overwrite=1" : ""}`, { method: "POST" }),
    putMapPgm: (name, pgmBytes) => call(`/maps/${enc(name)}/pgm`, { method: "PUT", bytes: pgmBytes }),
    removeMap: (name) => call(`/maps/${enc(name)}/delete`, { method: "POST" }),
    add: (host) => {
      const [h, port] = host.trim().split(":");
      return call(`/robots/add?host=${enc(h)}${port ? `&port=${enc(port)}` : ""}`, { method: "POST" });
    },
    adopt: (key) => call(`/robots/${enc(key)}/adopt`, { method: "POST" }),
    release: (key) => call(`/robots/${enc(key)}/release`, { method: "POST" }),
    forget: (key) => call(`/robots/${enc(key)}/forget`, { method: "POST" }),
    settings: (key, body) => call(`/robots/${enc(key)}/settings`, { method: "POST", body }),
    launch: (key, map, target = "real") => call(`/robots/${enc(key)}/launch?map=${enc(map)}&target=${enc(target)}`, { method: "POST" }),
    stopStack: (key) => call(`/robots/${enc(key)}/stop`, { method: "POST" }),
    command: (key, cmd) => call(`/robots/${enc(key)}/command`, { method: "POST", body: cmd }),
    commandAll: (cmd) => call("/command", { method: "POST", body: cmd }),
    dispatch: (task) => call("/tasks", { method: "POST", body: task }),
    provisionConnect: (body) => call("/provision/connect", { method: "POST", body }),
    provisionRun: (body) => call("/provision/run", { method: "POST", body }),
    job: (id, after = 0) => call(`/jobs/${enc(id)}?after=${after}`),
    deploy: (key) => call(`/robots/${enc(key)}/deploy`, { method: "POST" }),
  };
}

export const ROBOT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,15}$/;
export const ROBOT_ID_HINT = "1–16 letters, digits or '-', e.g. AMR-03.";
