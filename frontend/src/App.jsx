import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import TicketList from "./components/TicketList";
import DetailPanel from "./components/DetailPanel";
import NetworkMap from "./components/NetworkMap";

export default function App() {
  const [stats, setStats] = useState(null);
  const [tickets, setTickets] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [poles, setPoles] = useState([]);
  const [edges, setEdges] = useState([]);
  const [flash, setFlash] = useState(null);

  const selected = tickets.find((t) => t.id === selectedId) || null;

  const refresh = useCallback(async () => {
    const [s, t, p] = await Promise.all([api.stats(), api.tickets(), api.poles({ limit: 4000 })]);
    setStats(s);
    setTickets(t);
    setPoles(p);
    if (selectedId && !t.find((x) => x.id === selectedId) && t[0]) {
      setSelectedId(t[0].id);
    } else if (!selectedId && t[0]) {
      setSelectedId(t[0].id);
    }
    const dt = (t.find((x) => x.id === selectedId) || t[0])?.dt_id;
    const e = await api.edges(dt || undefined);
    setEdges(e.edges || []);
  }, [selectedId]);

  useEffect(() => {
    refresh().catch((e) => setFlash({ type: "err", text: e.message }));
    const id = setInterval(() => {
      refresh().catch(() => {});
    }, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 5000);
    return () => clearTimeout(t);
  }, [flash]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>KSPDB · SD07 Control Room</strong>
          <span>Fault localization — live/dark poles → span, PIN, ticket</span>
        </div>
        <div className="stats">
          <div className="stat">
            <b>{stats?.open_tickets ?? "—"}</b>
            <small>Open</small>
          </div>
          <div className="stat">
            <b>{stats?.currently_dark ?? "—"}</b>
            <small>Dark poles</small>
          </div>
          <div className="stat">
            <b>{stats?.poles ?? "—"}</b>
            <small>Poles</small>
          </div>
          <div className="stat">
            <b>{stats ? `${Math.round(stats.device_coverage * 100)}%` : "—"}</b>
            <small>Coverage</small>
          </div>
          <div className="stat">
            <b>
              {stats
                ? `${stats.inferred_topology_dts}/${stats.dts}`
                : "—"}
            </b>
            <small>Inferred DTs</small>
          </div>
        </div>
      </header>

      {flash && <div className={`flash ${flash.type}`} style={{ margin: "0.5rem 1rem" }}>{flash.text}</div>}

      <div className="layout">
        <aside className="panel">
          <h2>Incidents</h2>
          <TicketList tickets={tickets} selectedId={selectedId} onSelect={setSelectedId} />
        </aside>
        <NetworkMap poles={poles} edges={edges} ticket={selected} />
        <aside className="panel">
          <DetailPanel
            ticket={selected}
            onChanged={refresh}
            onFlash={setFlash}
          />
        </aside>
      </div>
    </div>
  );
}
