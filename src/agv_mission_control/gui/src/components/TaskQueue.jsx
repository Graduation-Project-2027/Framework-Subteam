/**
 * TaskQueue — Drag-reorder task list with add task modal and progress display.
 */
import { useState, useRef } from "react";

export default function TaskQueue({ missionState, sendCommand }) {
  const [showAdd, setShowAdd] = useState(false);
  const queue = missionState.queue || [];
  const currentTask = missionState.current_task;
  const dragItem = useRef(null);
  const dragOver = useRef(null);

  const handleDragStart = (idx) => {
    dragItem.current = idx;
  };

  const handleDragEnter = (idx) => {
    dragOver.current = idx;
  };

  const handleDragEnd = () => {
    if (dragItem.current === null || dragOver.current === null) return;
    const newOrder = [...queue];
    const [removed] = newOrder.splice(dragItem.current, 1);
    newOrder.splice(dragOver.current, 0, removed);
    sendCommand({ action: "reorder_tasks", order: newOrder.map((t) => t.id) });
    dragItem.current = null;
    dragOver.current = null;
  };

  const handleRemove = (id) => {
    sendCommand({ action: "remove_task", id });
  };

  return (
    <div className="panel" style={{ flex: 1 }}>
      <div className="panel__header">
        <span className="panel__title">📋 Task Queue ({queue.length})</span>
        <button className="btn btn--sm btn--primary" onClick={() => setShowAdd(true)}>
          + Add Task
        </button>
      </div>
      <div className="panel__body">
        {queue.length === 0 && (
          <div className="empty-state">
            <div className="empty-state__icon">📭</div>
            <span>No tasks in queue</span>
            <span style={{ fontSize: 11 }}>Add a transport task or double-click the map</span>
          </div>
        )}
        {queue.map((task, idx) => {
          const isActive = currentTask && currentTask.id === task.id;
          return (
            <div
              key={task.id}
              className={`task-item ${isActive ? "task-item--active" : ""} ${task.status === "done" ? "task-item--done" : ""}`}
              draggable
              onDragStart={() => handleDragStart(idx)}
              onDragEnter={() => handleDragEnter(idx)}
              onDragEnd={handleDragEnd}
              onDragOver={(e) => e.preventDefault()}
            >
              <span className="task-item__grip">⠿</span>
              <div className="task-item__info">
                <div className="task-item__name">
                  {task.from_name ? `${task.from_name} → ${task.to_name}` : task.to_name}
                </div>
                <div className="task-item__detail">
                  {task.type === "transport" ? "🔄 Transport" : "📍 Navigate"} · {task.status}
                </div>
                {isActive && (
                  <div className="progress">
                    <div className="progress__fill" style={{ width: `${(missionState.progress || 0) * 100}%` }} />
                  </div>
                )}
              </div>
              <button
                className="btn btn--sm btn--icon task-item__remove"
                onClick={() => handleRemove(task.id)}
                title="Remove"
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>

      {showAdd && <AddTaskModal missionState={missionState} sendCommand={sendCommand} onClose={() => setShowAdd(false)} />}
    </div>
  );
}

function AddTaskModal({ missionState, sendCommand, onClose }) {
  const locations = missionState.locations || {};
  const racks = locations.racks || [];
  const zones = locations.drop_zones || [];
  const [type, setType] = useState("transport");
  const [fromId, setFromId] = useState(racks[0]?.id || "");
  const [toId, setToId] = useState(zones[0]?.id || "");

  const allLocs = [
    ...(racks.map((r) => ({ ...r, kind: "rack" }))),
    ...(zones.map((z) => ({ ...z, kind: "zone" }))),
    ...(locations.dock ? [{ ...locations.dock, id: "dock", kind: "dock" }] : []),
  ];

  const handleAdd = () => {
    const from = allLocs.find((l) => l.id === fromId);
    const to = allLocs.find((l) => l.id === toId);
    if (type === "transport" && from && to) {
      sendCommand({
        action: "add_task",
        type: "transport",
        rack_id: from.id,
        from_name: from.name || from.id,
        to_name: to.name || to.id,
        // id MUST be included — the backend keys its free/occupied state
        // updates off from.id / to.id.  Without it R1 never goes green and
        // R8 never goes red after a transport.
        from: { id: from.id, name: from.name, x: from.x, y: from.y, yaw: from.yaw || 0 },
        to: { id: to.id, name: to.name, x: to.x, y: to.y, yaw: to.yaw || 0 },
      });
    } else if (type === "navigate" && to) {
      sendCommand({
        action: "add_task",
        type: "navigate",
        from_name: "",
        to_name: to.name || to.id,
        to: { x: to.x, y: to.y, yaw: to.yaw || 0 },
      });
    }
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__title">Add Task</div>
        <div className="modal__field">
          <label className="modal__label">Type</label>
          <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="transport">🔄 Transport (pick → place)</option>
            <option value="navigate">📍 Navigate (go to)</option>
          </select>
        </div>
        {type === "transport" && (
          <div className="modal__field">
            <label className="modal__label">Pick From</label>
            <select className="input" value={fromId} onChange={(e) => setFromId(e.target.value)}>
              {allLocs.map((l) => (
                <option key={l.id} value={l.id}>{l.name || l.id}</option>
              ))}
            </select>
          </div>
        )}
        <div className="modal__field">
          <label className="modal__label">{type === "transport" ? "Place At" : "Navigate To"}</label>
          <select className="input" value={toId} onChange={(e) => setToId(e.target.value)}>
            {allLocs.map((l) => (
              <option key={l.id} value={l.id}>{l.name || l.id}</option>
            ))}
          </select>
        </div>
        <div className="modal__actions">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" onClick={handleAdd}>Add</button>
        </div>
      </div>
    </div>
  );
}
