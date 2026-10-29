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

## Fleet

The laptop's view of every robot, in the same world: a white architectural model of the site on drafting paper.

- **Workspaces.** Top bar: `Fleet | <robot>`. Fleet shows every robot; the robot workspace is the single-robot mission control above, pointed at the chosen robot.
- **Layout.** Left: roster (in the fleet / found on the network / needs attention / set up a new Pi). Centre: 3D floor + fleet event log. Right: Robot · Dispatch · Layout · Maps. Below 1280 px all five share the left tabs; below 900 px the floor sits on top.
- **3D floor (React Three Fiber).** Paper floor printed from the map at true colours (unlit; shadows on a separate catcher, 3D view only), 1 m / 5 m rules. Only real walls (runs ≥ 0.4 m) are extruded in plaster `#D9DBE0`; rack-leg marks stay printed. Racks are ink frames, free sites hollow ink squares, zones dashed. Lanes are gold tape with rounded junctions; open ends carry a gold-ink stop bar, and while drawing a ring marks where an end will join. Robots are their black chassis with the red disc and a white heading bar; a carried rack rides on the robot. 3D for watching, Plan (orthographic) for drawing lanes.
- **Traffic language.** Each driving robot's reserved floor is a faint ink strip; the pair in conflict turns gold, the held robot gets a gold ring and a gold tag ("Holding for AMR-01"). Red stays reserved for E-stop and deadlocks. A live halo marks robots that are actually moving.
- **Robot glyph.** The chassis from above everywhere a robot is named: red sun = driving, gold ring = held, hollow = stopped or not answering, dashed = new robot.
- **Map sync cells.** Per robot: ink = same copy, gold = different, dashed = missing, red dot = running now.
- **Faces.** Every rack/zone draws an ink arrow from its front edge to a small ring on the lane: the one entry point. Hovering a site in the list turns its arrow red. A site not square to a lane is flagged in gold.
- **Schematic lanes.** In Edit nodes every node is a paper disc with an ink ring (gold ring = open end, ink fill = selected); while drawing, a gold-ink ring shows the node a click will link to (red = closes the loop). One-way lanes carry gold-ink chevrons.
- **Pi setup sheet.** The right drawer of the System sheet: Connect → Configure (facts card, Robot ID → hostname) → job (numbered steps with the live sun on the current one, ink log panel).
- E-stop all is never behind a confirmation.

## Motion

150–220 ms, ease-out. Only for state: tab change, inline editor open, queue reorder/remove, new log line, live halo on the moving robot. Respects `prefers-reduced-motion`.

## Components

Built on Radix primitives (`radix-ui`) styled with these tokens (`src/components/ui`): Button, IconButton, Tabs, Tooltip, Select, Segmented (ToggleGroup), Slider, Switch, Popover, ConfirmDialog, Dialog (sheet), DropdownMenu, Toast. Icons: lucide-react, 1.75 stroke.
