import { isDriving } from "../../lib/traffic";
import { cx } from "../../lib/cx";

/** Top-down chassis: the robot is the emblem. Red sun = driving, gold ring
 *  = held by traffic, hollow = not answering, dashed = new robot. */
export function RobotGlyph({ robot, size = 26 }) {
  const m = robot.mission;
  const tone = !robot.adopted ? "new" : robot.status !== "online" || !robot.stack?.running ? "off" : robot.hold ? "held"
    : m?.state === "ERROR" ? "fault" : isDriving(m) ? "live" : "idle";
  return (
    <span className={cx("rglyph", `rglyph--${tone}`)} style={{ "--g": `${size}px` }} aria-hidden="true">
      <i />
    </span>
  );
}
