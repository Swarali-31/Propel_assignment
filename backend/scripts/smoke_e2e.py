"""One-shot acceptance self-check against a running API."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def call(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        raise RuntimeError(f"{method} {path} -> {e.code}: {payload}") from e


def main():
    st = call("GET", "/api/network/stats")
    print("stats", st)
    assert st["poles"] > 1000, "network not seeded"

    # Clear path
    try:
        call("POST", "/api/simulator/reset")
    except Exception:
        try:
            call("POST", "/api/simulator/repair", {})
        except Exception:
            pass

    before = call("GET", "/api/network/stats")["open_tickets"]
    assert before == 0, before

    inj = call("POST", "/api/simulator/inject", {"fault_type": "span"})
    print("inject", inj["tickets_created"], "affected", inj["affected_count"])
    assert inj["affected_count"] >= 2
    assert len(inj["tickets_created"]) >= 1

    tickets = call("GET", "/api/tickets")
    # Prefer the ticket just created
    tid = inj["tickets_created"][0]
    t = next(x for x in tickets if x["id"] == tid)
    print(
        "ticket",
        t["asset_label"],
        "PIN",
        t["pincode"],
        "poles",
        t["affected_pole_count"],
        "conf",
        round(t["confidence"], 2),
        t["topology_source"],
    )
    assert t["pincode"] or t["lat"]
    assert t["affected_pole_count"] >= 1

    try:
        call("POST", f"/api/tickets/{tid}/actions", {"action": "resolve"})
        raise SystemExit("resolve should have been blocked while dark")
    except RuntimeError as e:
        assert "409" in str(e) or "telemetry_not_restored" in str(e)
        print("resolve blocked OK")

    poles = call("GET", "/api/network/poles?limit=800")
    live = next(
        p["id"]
        for p in poles
        if p["has_device"] and p["energized"] and not p["device_offline"]
    )
    kill = call("POST", "/api/simulator/kill-device", {"pole_id": live})
    print("kill tickets", kill["tickets_created"])
    assert kill["tickets_created"] == []

    sched = call("POST", "/api/simulator/scheduled-outage-demo")
    print("sched tickets", sched["tickets_created"])
    assert sched["tickets_created"] == []

    call("POST", "/api/simulator/repair", {})
    t2 = call("GET", f"/api/tickets/{tid}")
    print("after repair", t2["status"])
    assert t2["status"] == "verified"

    brief = call("POST", f"/api/tickets/{tid}/briefing")
    print("briefing", brief["source"], "chars", len(brief["briefing"]))

    # Three simultaneous DT faults should yield multiple tickets (not one blob)
    created = []
    for _ in range(3):
        r = call("POST", "/api/simulator/inject", {"fault_type": "dt"})
        created.extend(r["tickets_created"])
    created = list(dict.fromkeys(created))
    print("three DT injects ->", len(created), "tickets", created)
    assert len(created) >= 3
    open_now = call("GET", "/api/network/stats")["open_tickets"]
    print("PASS")
    print("open_tickets_now", open_now, "was", before)
    # 1 verified span (not open) + 3 DT tickets; allow a little slack
    assert open_now <= 6, open_now


if __name__ == "__main__":
    main()
