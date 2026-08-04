export default function TicketList({ tickets, selectedId, onSelect }) {
  if (!tickets.length) {
    return <div className="empty">No open incidents. Use the simulator to inject a fault.</div>;
  }
  return (
    <div>
      {tickets.map((t) => (
        <button
          key={t.id}
          className={`ticket ${selectedId === t.id ? "active" : ""}`}
          onClick={() => onSelect(t.id)}
        >
          <div className="ticket-top">
            <span className={`badge ${t.status}`}>{t.status.replace("_", " ")}</span>
            <span className="badge">{Math.round(t.confidence * 100)}%</span>
          </div>
          <div className="asset">{t.asset_label}</div>
          <div className="meta">
            {t.fault_type.toUpperCase()} · PIN {t.pincode || "?"} · {t.affected_pole_count} poles · ~
            {t.households_estimate} HH
          </div>
        </button>
      ))}
    </div>
  );
}
