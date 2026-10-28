import { useState, useCallback } from "react";

export const LAYER_GROUPS = [
  {
    title: "Map",
    items: [
      { id: "map", label: "Occupancy map", topic: "/map, /map_static" },
      { id: "grid", label: "Metre grid" },
    ],
  },
  {
    title: "Navigation",
    items: [
      { id: "globalCostmap", label: "Global costmap", topic: "/global_costmap/costmap", nav: true },
      { id: "localCostmap", label: "Local costmap", topic: "/local_costmap/costmap", nav: true },
      { id: "path", label: "Planned path", topic: "/plan", nav: true },
      { id: "footprint", label: "Footprint", topic: "/local_costmap/published_footprint", nav: true },
    ],
  },
  {
    title: "Sensors",
    items: [
      { id: "lidar", label: "Lidar, merged", topic: "/scan_merged" },
      { id: "lidarFront", label: "Front lidar", topic: "/scan" },
      { id: "lidarRear", label: "Rear lidar", topic: "/scan_rear" },
      { id: "dynamic", label: "Dynamic obstacles", topic: "Lidar hits off the map" },
      { id: "rackObstacles", label: "Rack legs", topic: "/rack_obstacles" },
    ],
  },
  {
    title: "Mission",
    items: [
      { id: "lanes", label: "Lanes" },
      { id: "sites", label: "Racks, zones and dock" },
      { id: "trail", label: "Robot trail" },
    ],
  },
];

export const DEFAULT_LAYERS = {
  map: true, grid: true,
  globalCostmap: false, localCostmap: false, path: true, footprint: false,
  lidar: true, lidarFront: false, lidarRear: false, dynamic: true, rackObstacles: false,
  lanes: true, sites: true, trail: true,
};

const KEY = "leverage.layers.v1";

export function useLayers() {
  const [layers, setLayers] = useState(() => {
    try { return { ...DEFAULT_LAYERS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { return DEFAULT_LAYERS; }
  });
  const setLayer = useCallback((id, on) => {
    setLayers((l) => {
      const next = { ...l, [id]: on };
      try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });
  }, []);
  return [layers, setLayer];
}

/** rosbridge topics the enabled layers need (see SENSOR_TOPICS in useROS). */
export function sensorTopicsFor(layers, mode) {
  const t = ["tf", "tfStatic", "map"];
  if (mode !== "mapping") t.push("mapStatic");
  if (layers.globalCostmap) t.push("globalCostmap");
  if (layers.localCostmap) t.push("localCostmap");
  if (layers.footprint) t.push("footprint");
  if (layers.lidar || layers.dynamic) t.push("scanMerged");
  if (layers.lidarFront) t.push("scanFront");
  if (layers.lidarRear) t.push("scanRear");
  if (layers.rackObstacles) t.push("rackCloud");
  return t;
}
