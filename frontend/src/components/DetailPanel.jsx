import { useState } from "react";
import { api } from "../api/client";

export default function DetailPanel({ ticket, onChanged, onFlash }) {
  const [busy, setBusy] = useState(false);
  const [briefing, setBriefing] = useState(ticket?.ai_briefing || "");

  async function act(action, extra = {}) {
    setBusy(true);
    try {
      await api.ticketAction(ticket.id, action, extra);
      onFlash({ type: "ok", text: `Ticket ${action} OK` });
      onChanged();
    } catch (e) {
      onFlash({
        type: "err",
        text: e.data?.detail?.message || e.message,
      });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function loadBriefing() {
    setBusy(true);
    try {
      const r = await api.briefing(ticket.id);
      setBriefing(r.briefing);
      onFlash({ type: "ok", text: `Briefing via ${r.source}` });
    } catch (e) {
      onFlash({ type: "err", text: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail">
      {/* Simulator first so it is always visible without scrolling */}
      <Simulator onFlash={onFlash} onChanged={onChanged} />

      <h2>Incident</h2>
      {!ticket ? (
        <div className="empty">
          No incident selected. Click <strong>Inject span fault</strong> above to create one.
        </div>
      ) : (
        <>
          <div className={`badge ${ticket.status}`}>{ticket.status.replace("_", " ")}</div>
          <div className="title">{ticket.asset_label}</div>
          <div className="coords">
            {ticket.lat.toFixed(5)}° N {ticket.lon.toFixed(5)}° E · PIN {ticket.pincode || "unknown"}
          </div>
          <div className="confidence" title={ticket.confidence_reason}>
            <span style={{ width: `${Math.round(ticket.confidence * 100)}%` }} />
          </div>
          <dl className="kv">
            <dt>Type</dt>
            <dd>{ticket.fault_type}</dd>
            <dt>Feeder / DT</dt>
            <dd>
              {ticket.feeder_id}
              {ticket.dt_id ? ` / ${ticket.dt_id}` : ""}
            </dd>
            <dt>Affected</dt>
            <dd>
              {ticket.affected_pole_count} poles · ~{ticket.households_estimate} households
            </dd>
            <dt>Topology</dt>
            <dd>{ticket.topology_source}</dd>
            <dt>Why</dt>
            <dd>{ticket.confidence_reason}</dd>
            <dt>Crew</dt>
            <dd>{ticket.crew_name || "—"}</dd>
          </dl>

          <div className="actions">
            <button
              className="btn"
              disabled={busy || ticket.status !== "detected"}
              onClick={() => act("acknowledge")}
            >
              Acknowledge
            </button>
            <button
              className="btn"
              disabled={busy || !["detected", "acknowledged"].includes(ticket.status)}
              onClick={() => act("assign", { crew_name: "Line Crew 7" })}
            >
              Assign crew
            </button>
            <button
              className="btn primary"
              disabled={busy || ["verified", "closed"].includes(ticket.status)}
              onClick={() => act("resolve")}
            >
              Mark resolved
            </button>
            <button
              className="btn"
              disabled={busy || ticket.status !== "verified"}
              onClick={() => act("close")}
            >
              Close
            </button>
            <button className="btn" disabled={busy} onClick={loadBriefing}>
              AI crew briefing
            </button>
          </div>

          {ticket.false_resolve_attempts > 0 && (
            <div className="flash err">
              Resolve blocked {ticket.false_resolve_attempts}× — poles still dark in telemetry.
            </div>
          )}

          {(briefing || ticket.ai_briefing) && (
            <div className="briefing">{briefing || ticket.ai_briefing}</div>
          )}
        </>
      )}
    </div>
  );
}

function Simulator({ onFlash, onChanged }) {
  const [busy, setBusy] = useState(false);

  async function run(label, fn) {
    setBusy(true);
    try {
      const r = await fn();
      onFlash({
        type: "ok",
        text: `${label}: ${JSON.stringify(
          r.tickets_created
            ? { tickets: r.tickets_created, affected: r.affected_count ?? r.note }
            : r
        ).slice(0, 180)}`,
      });
      onChanged();
    } catch (e) {
      onFlash({ type: "err", text: `${label} failed: ${e.message}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sim sim-top">
      <h2>Fault simulator</h2>
      <p>Use these buttons to create faults and test the system.</p>
      <div className="actions">
        <button
          className="btn primary"
          disabled={busy}
          onClick={() =>
            run("Span fault", async () => {
              const s = await api.suggestSpan();
              return api.inject({
                fault_type: "span",
                downstream_pole_id: s.downstream_pole_id,
              });
            })
          }
        >
          Inject span fault
        </button>
        <button className="btn" disabled={busy} onClick={() => run("DT fault", () => api.inject({ fault_type: "dt" }))}>
          Inject DT fault
        </button>
        <button
          className="btn"
          disabled={busy}
          onClick={() => run("Feeder fault", () => api.inject({ fault_type: "feeder" }))}
        >
          Inject feeder fault
        </button>
        <button className="btn" disabled={busy} onClick={() => run("Repair last", () => api.repair({}))}>
          Repair last fault
        </button>
        <button
          className="btn danger"
          disabled={busy}
          onClick={() =>
            run("Kill device", async () => {
              const poles = await api.poles({ limit: 200 });
              const live = poles.find((p) => p.has_device && p.energized && !p.device_offline);
              if (!live) throw new Error("No live device found — try Reset network first");
              return api.killDevice(live.id);
            })
          }
        >
          Kill device (no ticket)
        </button>
        <button className="btn" disabled={busy} onClick={() => run("Scheduled outage", () => api.scheduledDemo())}>
          Scheduled outage demo
        </button>
        <button className="btn danger" disabled={busy} onClick={() => run("Reset network", () => api.resetSim())}>
          Reset network
        </button>
      </div>
    </div>
  );
}
