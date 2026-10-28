/**
 * LocationEditor — Sidebar panel for managing rack/drop-zone locations
 * and dock configuration. "Pick on Map" hides the modal so the map
 * is accessible for clicking. Click+drag sets position+heading.
 */
import { useState } from "react";

export default function LocationEditor({
  missionState,
  sendCommand,
  onPickFromMap,
  onStartLaneDraft,
  laneDraftActive,
  onFinishLaneDraft,
  onCancelLaneDraft,
  onDeleteLane,
}) {
  const [editModal, setEditModal] = useState(null);
  const [dockModal, setDockModal] = useState(false);

  const locations = missionState.locations || { racks: [], drop_zones: [], dock: {}, lanes: [] };

  const handleSave = (type, index, data) => {
    const updated = { ...locations };
    if (index === -1) {
      updated[type] = [...(updated[type] || []), { ...data, id: data.id || `${type}_${Date.now()}` }];
    } else {
      updated[type] = [...(updated[type] || [])];
      updated[type][index] = { ...updated[type][index], ...data };
    }
    sendCommand({ action: "update_locations", racks: updated.racks, drop_zones: updated.drop_zones });
    setEditModal(null);
  };

  const handleRemove = (type, index) => {
    const updated = { ...locations };
    updated[type] = updated[type].filter((_, i) => i !== index);
    sendCommand({ action: "update_locations", racks: updated.racks, drop_zones: updated.drop_zones });
  };

  const handleDockSave = (data) => {
    sendCommand({ action: "update_dock", ...data });
    setDockModal(false);
  };

  return (
    <div className="panel" style={{ flex: 1 }}>
      <div className="panel__header">
        <span className="panel__title">📍 Locations</span>
        <div style={{ display: "flex", gap: 4 }}>
          <button className="btn btn--sm" onClick={() => setEditModal({ type: "racks", index: -1, data: { name: "", x: 0, y: 0, yaw: 0, state: "occupied" } })}>
            + Rack
          </button>
          <button className="btn btn--sm" onClick={() => setEditModal({ type: "drop_zones", index: -1, data: { name: "", x: 0, y: 0, yaw: 0, state: "free" } })}>
            + Zone
          </button>
          <button
            className={`btn btn--sm ${laneDraftActive ? "btn--primary" : ""}`}
            onClick={() => {
              if (laneDraftActive) onFinishLaneDraft?.();
              else onStartLaneDraft?.();
            }}
          >
            {laneDraftActive ? "✓ Finish" : "+ Lane"}
          </button>
          {laneDraftActive && (
            <button className="btn btn--sm btn--danger" onClick={() => onCancelLaneDraft?.()}>
              ✕
            </button>
          )}
        </div>
      </div>
      <div className="panel__body">
        {/* Dock */}
        <div className="location-item" onClick={() => setDockModal(true)}>
          <span className="location-item__dot location-item__dot--dock" />
          <span className="location-item__name">{locations.dock?.name || "Dock"}</span>
          <span className="location-item__coords">
            ({locations.dock?.x?.toFixed(1)}, {locations.dock?.y?.toFixed(1)})
          </span>
        </div>
        <div className="divider" />

        {/* Racks */}
        {(locations.racks || []).map((r, i) => (
          <div key={r.id || i} className="location-item">
            <span className="location-item__dot location-item__dot--rack" />
            <span className="location-item__name" onClick={() => setEditModal({ type: "racks", index: i, data: r })}>
              {r.name || r.id}
            </span>
            <span className="location-item__coords">
              ({r.x?.toFixed(1)}, {r.y?.toFixed(1)})
            </span>
            <button className="btn btn--sm btn--danger btn--icon" onClick={() => handleRemove("racks", i)} style={{ marginLeft: 4, padding: "2px 6px", fontSize: 10 }}>
              ✕
            </button>
          </div>
        ))}

        {/* Drop Zones */}
        {(locations.drop_zones || []).map((z, i) => (
          <div key={z.id || i} className="location-item">
            <span className="location-item__dot location-item__dot--zone" />
            <span className="location-item__name" onClick={() => setEditModal({ type: "drop_zones", index: i, data: z })}>
              {z.name || z.id}
            </span>
            <span className="location-item__coords">
              ({z.x?.toFixed(1)}, {z.y?.toFixed(1)})
            </span>
            <button className="btn btn--sm btn--danger btn--icon" onClick={() => handleRemove("drop_zones", i)} style={{ marginLeft: 4, padding: "2px 6px", fontSize: 10 }}>
              ✕
            </button>
          </div>
        ))}

        {/* Lanes */}
        {(locations.lanes || []).map((lane) => (
          <div key={lane.id} className="location-item">
            <span
              className="location-item__dot"
              style={{ background: "#06b6d4" }}
            />
            <span className="location-item__name">
              {lane.name || lane.id}
            </span>
            <span className="location-item__coords">
              {lane.points?.length || 0} pts
            </span>
            <button
              className="btn btn--sm btn--danger btn--icon"
              onClick={() => onDeleteLane?.(lane.id)}
              style={{ marginLeft: 4, padding: "2px 6px", fontSize: 10 }}
            >
              ✕
            </button>
          </div>
        ))}

        {locations.racks?.length === 0 && locations.drop_zones?.length === 0 && (locations.lanes?.length || 0) === 0 && (
          <div className="empty-state">
            <div className="empty-state__icon">📦</div>
            <span>No locations defined yet</span>
          </div>
        )}
      </div>

      {/* Edit Modal */}
      {editModal && (
        <EditModal
          title={editModal.index === -1 ? "Add Location" : "Edit Location"}
          data={editModal.data}
          onSave={(d) => handleSave(editModal.type, editModal.index, d)}
          onCancel={() => setEditModal(null)}
          onPickFromMap={onPickFromMap}
        />
      )}

      {/* Dock Modal */}
      {dockModal && (
        <EditModal
          title="Edit Dock Position"
          data={locations.dock || { name: "Dock", x: 0, y: 0, yaw: 0 }}
          onSave={handleDockSave}
          onCancel={() => setDockModal(false)}
          onPickFromMap={onPickFromMap}
        />
      )}
    </div>
  );
}

function EditModal({ title, data, onSave, onCancel, onPickFromMap }) {
  const [form, setForm] = useState({ ...data });
  const [minimized, setMinimized] = useState(false);

  const handlePickFromMap = () => {
    setMinimized(true);
    onPickFromMap?.((pos) => {
      setForm((f) => ({
        ...f,
        x: pos.x,
        y: pos.y,
        yaw: pos.yaw !== undefined ? pos.yaw : f.yaw,
      }));
      setMinimized(false);
    });
  };

  // When minimized, show a small floating chip instead of the full modal
  if (minimized) {
    return (
      <div className="modal-mini">
        <span>📍 Click &amp; drag on map to set pose…</span>
        <button
          className="btn btn--sm"
          onClick={() => setMinimized(false)}
          style={{ marginLeft: 8 }}
        >
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__title">{title}</div>
        <div className="modal__field">
          <label className="modal__label">Name</label>
          <input
            className="input"
            value={form.name || ""}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="modal__field">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <label className="modal__label" style={{ margin: 0 }}>Position &amp; Heading</label>
            <button
              className="btn btn--sm btn--primary"
              onClick={handlePickFromMap}
              style={{ fontSize: 11, padding: "3px 10px" }}
            >
              📍 Pick on Map
            </button>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <div className="modal__field" style={{ flex: 1 }}>
            <label className="modal__label">X (m)</label>
            <input
              className="input"
              type="number"
              step="0.1"
              value={form.x ?? 0}
              onChange={(e) => setForm({ ...form, x: parseFloat(e.target.value) || 0 })}
            />
          </div>
          <div className="modal__field" style={{ flex: 1 }}>
            <label className="modal__label">Y (m)</label>
            <input
              className="input"
              type="number"
              step="0.1"
              value={form.y ?? 0}
              onChange={(e) => setForm({ ...form, y: parseFloat(e.target.value) || 0 })}
            />
          </div>
          <div className="modal__field" style={{ flex: 1 }}>
            <label className="modal__label">Yaw (°)</label>
            <input
              className="input"
              type="number"
              step="5"
              value={Math.round((form.yaw ?? 0) * 180 / Math.PI)}
              onChange={(e) => setForm({ ...form, yaw: (parseFloat(e.target.value) || 0) * Math.PI / 180 })}
            />
          </div>
        </div>
        {/* State toggle — only relevant for racks/zones (not the dock) */}
        {("state" in form || form.state !== undefined) && (
          <div className="modal__field">
            <label className="modal__label">State</label>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className={`btn btn--sm ${(form.state || "occupied") === "occupied" ? "btn--danger" : ""}`}
                onClick={() => setForm({ ...form, state: "occupied" })}
                style={{ flex: 1 }}
              >
                🟥 Occupied
              </button>
              <button
                type="button"
                className={`btn btn--sm ${form.state === "free" ? "btn--primary" : ""}`}
                onClick={() => setForm({ ...form, state: "free" })}
                style={{ flex: 1 }}
              >
                🟩 Free
              </button>
            </div>
          </div>
        )}
        <div className="modal__actions">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className="btn btn--primary" onClick={() => onSave(form)}>Save</button>
        </div>
      </div>
    </div>
  );
}
