# Design — LEVERAGE Mission Control

Mode: **Operate**. The tool disappears into the task; brand lives in precise details.

## World

A printed engineering floor plan in E-JUST colours. White paper, black ink, one red sun.

- **Signature move — the robot is the emblem.** On the map the AMR is drawn as its true black chassis with the E-JUST red disc at its centre. The same red disc marks "what the robot is doing now" everywhere else (the active task row, the current mission step, the live state). Red is otherwise reserved for E-stop and errors.
- **Occupancy is shape, not colour.** Occupied rack = solid ink square; free = hollow outline. Readable in greyscale, on a projector and for colour-blind viewers.
- **Lanes are floor tape.** Lanes are drawn in E-JUST gold, like warehouse floor markings. Gold also means caution (paused, preview).

## Tokens

| Role | Value |
| --- | --- |
| Paper (surface) | `#FFFFFF` |
| Ground (rails, second neutral) | `#F3F4F6` |
| Ink (text, primary buttons, occupied) | `#141416` |
| Muted text | `#5C5F66` |
| Line / rule / control | `#E2E3E7` / `#ECEDF0` / `#C8CBD0` |
| Signal red (robot, E-stop) | `#EA232B`; text `#C4161F` |
| Gold (lanes, caution) | `#DE9242`; text `#8A5410` |
| Positive (connected, done) | `#1F7A4D` |

Type: **Archivo** (variable, self-hosted) for everything; the LEVERAGE wordmark uses its expanded width. Tabular figures on every number. Scale 11 / 12 / 13 / 14 / 16 / 20 px. Weights 400 / 500 / 600, 700 for the wordmark and state only.

Radius: tight — 6 px panels, 6 px controls, 4 px chips. Edges are 1 px lines; shadows only on things that float (menus, tooltips, map overlays).

Spacing: 4 px base; one gap (12 px) inside panels, 16 px panel padding.

## Layout

```
┌ top bar: emblem LEVERAGE · connection · system ─────── mission controls · E-STOP ┐
├ Tasks | Locations ┬──────────── floor plan (map) ─────────────┬─ Robot ───────────┤
│ composer          │ tools         mode banner                 │ state + steps     │
│ queue             │                                           │ telemetry         │
│                   │ legend                    scale · cursor  │ drive · lift      │
│                   ├──────────── event log (collapsible) ──────┤ speed             │
└───────────────────┴───────────────────────────────────────────┴───────────────────┘
```

- ≥1280 px: three columns. 900–1279 px: Robot folds into the left tabs. <900 px: map on top, one tabbed panel below; E-stop stays in the top bar.

## Robot computer and RViz layers

- **System sheet** (right drawer from the top-bar stack button): target, mode, map, RViz, launch/restart/stop, live launcher output on an ink panel (the one place monospace is used: it is literal process output).
- **Maps tab**: rows with a real thumbnail of the map, tags (In use = ink, Default, Edited, No pose graph = gold), primary "Navigate with …", the rest in a menu. Delete is undoable via toast.
- **Overlays on the map** are paper cards with the float shadow, top-centre: the Mapping bar (live red sun, explored area, save) and the editor toolbar (tool, paint, size, undo/redo, save). Transient modes (pick, lane) use the ink banner.
- **Layers** popover mirrors RViz displays, grouped Map / Navigation / Sensors / Mission, each a labelled switch with its topic. Lanes also get a direct toggle in the map toolbar.
- **Sensor colours** stay inside the world: lidar = red points (rear lidar gold-ink), dynamic obstacles = red outlined boxes, costmap = gold ramp to red at inscribed/lethal, footprint = dashed ink, rack legs = gold-ink points.
- **Lanes are road-like**: straight floor-tape pieces plus rounded turning arcs at every bend and junction (same radius as the robot, from `lane_corner_radius`); junctions get a small paper node.

## Motion

150–220 ms, ease-out. Only for state: tab change, inline editor open, queue reorder/remove, new log line, live halo on the moving robot. Respects `prefers-reduced-motion`.

## Components

Built on Radix primitives (`radix-ui`) styled with these tokens (`src/components/ui`): Button, IconButton, Tabs, Tooltip, Select, Segmented (ToggleGroup), Slider, Switch, Popover, ConfirmDialog, Dialog (sheet), DropdownMenu, Toast. Icons: lucide-react, 1.75 stroke.
