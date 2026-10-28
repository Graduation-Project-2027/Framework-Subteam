/**
 * Preview fixture: the team's real layout from config/locations.json,
 * used only by Preview mode (no robot, no ROS).
 */
export const PREVIEW_LOCATIONS = {
  dock: { x: -1.55, y: 1.36, yaw: -0.04, name: "Home Dock" },
  racks: [
    { id: "racks_1778455475870", name: "R1", x: 0.46, y: 4.94, yaw: -1.544, state: "occupied" },
    { id: "racks_1778455491439", name: "R2", x: 1.74, y: 4.95, yaw: -1.549, state: "occupied" },
    { id: "racks_1778455508686", name: "R3", x: 0.48, y: 3.64, yaw: 1.603, state: "occupied" },
    { id: "racks_1778455644108", name: "R4", x: 0.57, y: 8.28, yaw: 1.596, state: "occupied" },
    { id: "racks_1778455657431", name: "R5", x: 1.72, y: 8.22, yaw: 1.588, state: "occupied" },
    { id: "racks_1778455581349", name: "R6", x: 2.94, y: 8.28, yaw: 1.571, state: "occupied" },
    { id: "racks_1778455594174", name: "R7", x: 4.1, y: 8.26, yaw: 1.553, state: "occupied" },
    { id: "racks_1778455609165", name: "R8", x: 5.28, y: 8.26, yaw: 1.562, state: "free" },
  ],
  drop_zones: [],
  lanes: [],
};
