const BASE = "https://propel-assignment-hqu6.onrender.com";

async function req(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const err = new Error(data?.detail?.message || data?.detail || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  meta: () => req("/api/meta"),
  stats: () => req("/api/network/stats"),
  tickets: () => req("/api/tickets"),
  ticket: (id) => req(`/api/tickets/${id}`),
  ticketAction: (id, action, extra = {}) =>
    req(`/api/tickets/${id}/actions`, {
      method: "POST",
      body: JSON.stringify({ action, ...extra }),
    }),
  briefing: (id) => req(`/api/tickets/${id}/briefing`, { method: "POST" }),
  poles: (params = {}) => {
    const q = new URLSearchParams(params).toString();
    return req(`/api/network/poles${q ? `?${q}` : ""}`);
  },
  edges: (dtId) => req(`/api/network/edges${dtId ? `?dt_id=${dtId}` : ""}`),
  dts: () => req("/api/network/dts"),
  scheduled: () => req("/api/scheduled-outages"),
  simState: () => req("/api/simulator/state"),
  suggestSpan: () => req("/api/simulator/suggest-span"),
  inject: (body) =>
    req("/api/simulator/inject", { method: "POST", body: JSON.stringify(body) }),
  repair: (body = {}) =>
    req("/api/simulator/repair", { method: "POST", body: JSON.stringify(body) }),
  killDevice: (pole_id) =>
    req("/api/simulator/kill-device", {
      method: "POST",
      body: JSON.stringify({ pole_id }),
    }),
  scheduledDemo: () => req("/api/simulator/scheduled-outage-demo", { method: "POST" }),
  resetSim: () => req("/api/simulator/reset", { method: "POST" }),
};
