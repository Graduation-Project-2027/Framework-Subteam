import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { RefreshCw, FlaskConical, Navigation, Radar, Server } from "lucide-react";
import { Button } from "./ui";

function useCountdown(at) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!at) return undefined;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [at]);
  return at ? Math.max(0, Math.ceil((at - now) / 1000)) : null;
}

/** Shown over the map while there is no robot to talk to, with the next step. */
export default function ConnectionNotice({ mode, status, url, retryAt, onRetry, onPreview, stack, prefs, onOpenSystem }) {
  const secs = useCountdown(retryAt);
  const s = stack.status;
  const preview = mode === "preview";
  const launch = (opts) => stack.launch({ target: prefs.target, rviz: prefs.rviz, ...opts }).catch(() => onOpenSystem());
  const defaultMap = s?.default_map;

  let title, body, actions;
  if (s?.running) {
    title = s.mode === "mapping" ? "Starting mapping…" : `Starting navigation on ${s.map}…`;
    body = preview
      ? "The simulated stack is coming up."
      : <>The stack is running; waiting for rosbridge at <code translate="no">{url}</code>.{secs != null ? ` Retrying in ${secs} s.` : ""}</>;
    actions = (
      <>
        {!preview && <Button variant="secondary" icon={RefreshCw} onClick={onRetry} disabled={status === "connecting"}>Retry now</Button>}
        <Button variant="secondary" icon={Server} onClick={onOpenSystem}>Show output</Button>
      </>
    );
  } else if (stack.reachable) {
    title = "The robot stack is stopped";
    body = defaultMap
      ? <>Start navigation on <strong>{defaultMap}</strong> to run missions, or start mapping to make a new map. {prefs.target === "sim" ? "Target: simulation." : "Target: real robot."}</>
      : "There is no saved map yet. Start mapping, drive the robot around the site, then save the map.";
    actions = (
      <>
        {defaultMap && <Button variant="primary" icon={Navigation} disabled={!!stack.busy} onClick={() => launch({ mode: "navigation", map: defaultMap })}>Navigate on {defaultMap}</Button>}
        <Button variant={defaultMap ? "secondary" : "primary"} icon={Radar} disabled={!!stack.busy} onClick={() => launch({ mode: "mapping" })}>Start mapping</Button>
        <Button variant="ghost" icon={Server} onClick={onOpenSystem}>More options</Button>
      </>
    );
  } else {
    const connecting = status === "connecting";
    title = connecting ? "Connecting to the robot…" : "Robot not connected";
    body = (
      <>
        Mission control talks to the robot through rosbridge at <code translate="no">{url}</code>.
        {connecting ? " Waiting for it to answer." : secs != null ? ` Retrying in ${secs} s.` : " Trying again…"}
        <span className="notice__how"> Run <code translate="no">launcher_server.py</code> on the robot to start the stack from here.</span>
      </>
    );
    actions = (
      <>
        <Button variant="secondary" icon={RefreshCw} onClick={onRetry} disabled={connecting || (status === "offline" && !retryAt)}>Retry now</Button>
        <Button variant="primary" icon={FlaskConical} onClick={onPreview}>Open preview</Button>
      </>
    );
  }

  return (
    <motion.div
      className="notice"
      role="status"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
    >
      <h2 className="notice__title">{title}</h2>
      <p>{body}</p>
      <div className="notice__actions">{actions}</div>
      {!preview && !stack.reachable && <p className="notice__foot">Preview simulates the robot, its maps and sensors on your real floor plan. Nothing is sent to hardware.</p>}
    </motion.div>
  );
}
