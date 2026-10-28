import { Popover, Switch } from "radix-ui";
import { Layers } from "lucide-react";
import { IconButton } from "../ui";
import { LAYER_GROUPS } from "../../lib/layers";

/** RViz-style display toggles; each one subscribes its topic only while on. */
export default function LayersMenu({ layers, onLayer, stackMode }) {
  const on = Object.values(layers).filter(Boolean).length;
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <IconButton label="Layers" side="right" icon={Layers} variant="float" size="sm" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="layers" side="right" align="start" sideOffset={8} collisionPadding={8}>
          <div className="layers__head">
            <h2>Layers</h2>
            <span className="count num">{on} shown</span>
          </div>
          {LAYER_GROUPS.map((g) => (
            <section key={g.title} className="layers__group" aria-label={g.title}>
              <h3>{g.title}</h3>
              {g.items.map((it) => {
                const id = `layer-${it.id}`;
                const off = it.nav && stackMode === "mapping";
                return (
                  <div key={it.id} className="layers__row">
                    <label htmlFor={id}>
                      <span className="layers__label">{it.label}</span>
                      {it.topic && <span className="layers__topic" translate="no">{off ? "Not in mapping mode" : it.topic}</span>}
                    </label>
                    <Switch.Root id={id} className="switch" checked={!!layers[it.id]} onCheckedChange={(v) => onLayer(it.id, v)}>
                      <Switch.Thumb className="switch__thumb" />
                    </Switch.Root>
                  </div>
                );
              })}
            </section>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
