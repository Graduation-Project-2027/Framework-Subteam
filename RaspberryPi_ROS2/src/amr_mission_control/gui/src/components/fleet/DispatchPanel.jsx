import { useState, memo } from "react";
import { Switch } from "radix-ui";
import { ArrowRight, Send } from "lucide-react";
import { Button, Segmented, Select } from "../ui";
import { RobotGlyph } from "./common";
import { taskTitle } from "../../lib/mission";
import { useToast } from "../../lib/toast";

function DispatchPanel({ fleet, onSelect }) {
  const toast = useToast();
  const layout = fleet.layout;
  const [type, setType] = useState("transport");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [robot, setRobot] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState(null);

  const robots = fleet.state.robots.filter((r) => r.adopted);
  const linked = robots.filter((r) => r.linked && r.stack?.mode === "navigation");
  const sites = [...(layout?.racks || []).map((s) => ({ ...s, kind: "Rack" })), ...(layout?.drop_zones || []).map((s) => ({ ...s, kind: "Zone" }))];
  // Sites another task already uses are offered last, with a note.
  const busySites = new Set(robots.flatMap((r) => [r.mission?.task?.rack_id, r.mission?.task?.to?.id]).filter(Boolean));
  const opt = (s) => ({ value: s.id, label: s.name, hint: busySites.has(s.id) ? "in a task" : s.kind });
  const sources = sites.filter((s) => s.state === "occupied").map(opt);
  const dests = sites.filter((s) => type === "navigate" || (s.state !== "occupied" && s.id !== from)).map(opt);
  const src = sources.some((o) => o.value === from) ? from : "";
  const dst = dests.some((o) => o.value === to) ? to : "";
  const ready = dst && (type === "navigate" || src) && linked.length > 0;

  const go = async () => {
    setBusy(true);
    try {
      const res = await fleet.act((c) => c.dispatch({ type, from_id: src, to_id: dst, robot }));
      setLast(res);
      setFrom("");
      setTo("");
    } catch (e) {
      toast({ title: "Not dispatched", body: e.message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const working = robots.filter((r) => r.mission?.task || r.mission?.queue);

  return (
    <div className="panel">
      <div className="composer">
        <Segmented value={type} onValueChange={setType} aria-label="Task type"
          options={[{ value: "transport", label: "Move rack" }, { value: "navigate", label: "Go to" }]} />
        {type === "transport" ? (
          <div className="composer__route">
            <label className="field"><span className="field__label">Pick up</span>
              <Select value={src} onValueChange={setFrom} options={sources} placeholder={sources.length ? "Occupied site" : "No racks"} aria-label="Pick up from" />
            </label>
            <ArrowRight className="composer__arrow" size={16} strokeWidth={1.75} aria-hidden="true" />
            <label className="field"><span className="field__label">Drop at</span>
              <Select value={dst} onValueChange={setTo} options={dests} placeholder={dests.length ? "Free site" : "No free site"} aria-label="Drop at" />
            </label>
          </div>
        ) : (
          <label className="field"><span className="field__label">Destination</span>
            <Select value={dst} onValueChange={setTo} options={dests} placeholder="Site" aria-label="Destination" />
          </label>
        )}
        <label className="field"><span className="field__label">Robot</span>
          <Select value={robot} onValueChange={setRobot} aria-label="Robot"
            options={[{ value: "auto", label: "Best free robot", hint: "nearest, least busy" },
              ...linked.map((r) => ({ value: r.key, label: r.label, hint: r.mission?.state === "IDLE" ? "idle" : "busy" }))]} />
        </label>
        <Button className="composer__submit" variant="primary" icon={Send} disabled={!ready || busy} onClick={go}>
          {busy ? "Dispatching…" : "Dispatch"}
        </Button>
        {!linked.length && <p className="hint">No robot is linked and navigating, so there is no one to send.</p>}
        {last && (
          <p className="assign" role="status">
            <b>{last.label}</b> takes it: {last.reason}.
          </p>
        )}
        {fleet.preview && (
          <label className="switchrow">
            <span>
              <span className="switchrow__title">Demo traffic</span>
              <span className="hint">Idle simulated robots pick up random transports.</span>
            </span>
            <Switch.Root className="switch" checked={!!fleet.state.preview?.autopilot}
              onCheckedChange={(v) => { fleet.client.setAutopilot(v); }} aria-label="Demo traffic">
              <Switch.Thumb className="switch__thumb" />
            </Switch.Root>
          </label>
        )}
      </div>

      <section aria-labelledby="work-h">
        <div className="section-head"><h2 id="work-h">Working now</h2><span className="count num">{working.length}</span></div>
        {working.length ? (
          <ul className="worklist">
            {working.map((r) => (
              <li key={r.key}>
                <button type="button" className="workrow" onClick={() => onSelect(r.key)}>
                  <RobotGlyph robot={r} size={20} />
                  <span className="workrow__id">{r.label}</span>
                  <span className="workrow__task">{r.mission?.task ? taskTitle(r.mission.task) : "Between tasks"}</span>
                  <span className="workrow__q num">{r.mission?.queue ? `+${Math.max(0, r.mission.queue - (r.mission.task ? 1 : 0))}` : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty empty--row">No tasks running. Dispatched tasks appear here per robot.</p>
        )}
      </section>
    </div>
  );
}

export default memo(DispatchPanel);
