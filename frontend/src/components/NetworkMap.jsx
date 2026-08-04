import { useEffect, useMemo } from "react";
import { MapContainer, TileLayer, CircleMarker, Polyline, Popup, useMap } from "react-leaflet";

function FitBounds({ points, ticket }) {
  const map = useMap();
  useEffect(() => {
    if (ticket) {
      map.flyTo([ticket.lat, ticket.lon], 16, { duration: 0.8 });
      return;
    }
    if (points.length > 2) {
      const lats = points.map((p) => p.lat);
      const lons = points.map((p) => p.lon);
      map.fitBounds(
        [
          [Math.min(...lats), Math.min(...lons)],
          [Math.max(...lats), Math.max(...lons)],
        ],
        { padding: [40, 40] }
      );
    }
  }, [map, points, ticket]);
  return null;
}

export default function NetworkMap({ poles, edges, ticket }) {
  const center = useMemo(() => {
    if (ticket) return [ticket.lat, ticket.lon];
    if (poles.length) return [poles[0].lat, poles[0].lon];
    return [12.95, 77.59];
  }, [poles, ticket]);

  const focusPoles = useMemo(() => {
    if (!ticket) return poles.slice(0, 800);
    const set = new Set(ticket.affected_pole_ids || []);
    const nearby = poles.filter((p) => p.dt_id === ticket.dt_id || set.has(p.id));
    return nearby.length ? nearby : poles.slice(0, 400);
  }, [poles, ticket]);

  return (
    <div className="map-wrap">
      <div className="map-overlay">
        {ticket
          ? `Focus: ${ticket.asset_label} · ${ticket.affected_pole_count} poles dark`
          : "Subdivision map · dark poles in red · select a ticket to zoom"}
      </div>
      <MapContainer center={center} zoom={13} style={{ height: "100%", width: "100%" }}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitBounds points={focusPoles} ticket={ticket} />
        {edges.map((e, i) => (
          <Polyline
            key={`${e.from}-${e.to}-${i}`}
            positions={[e.a, e.b]}
            pathOptions={{
              color: e.dark ? "#e85d4c" : "#3d7a5c",
              weight: e.dark ? 3 : 1.5,
              opacity: e.dark ? 0.9 : 0.45,
            }}
          />
        ))}
        {focusPoles.map((p) => (
          <CircleMarker
            key={p.id}
            center={[p.lat, p.lon]}
            radius={p.energized ? 4 : 7}
            pathOptions={{
              color: "#0f1c18",
              weight: 1,
              fillColor: p.device_offline ? "#6b7280" : p.energized ? "#3dba7a" : "#e85d4c",
              fillOpacity: 0.95,
            }}
          >
            <Popup>
              <strong>{p.id}</strong>
              <br />
              {p.energized ? "LIVE" : "DARK"}
              {p.device_offline ? " · device offline" : ""}
              <br />
              DT {p.dt_id}
              <br />
              PIN {p.pincode || "—"}
            </Popup>
          </CircleMarker>
        ))}
        {ticket && (
          <CircleMarker
            center={[ticket.lat, ticket.lon]}
            radius={12}
            pathOptions={{ color: "#f0c14b", weight: 3, fillColor: "#d4a017", fillOpacity: 0.35 }}
          >
            <Popup>
              Fault location
              <br />
              {ticket.asset_label}
            </Popup>
          </CircleMarker>
        )}
      </MapContainer>
    </div>
  );
}
