"""Topology inference for DTs that lack recorded pole ordering.

Approach: Prim-style MST rooted at the DT location. Edges are weighted by
haversine distance. This recovers the true radial order well when poles are
laid out along streets; it fails when two parallel laterals run close together
or when GPS noise folds a branch onto the main run.

We store the result in Pole.effective_parent_id and tag topology_source.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class PoleNode:
    id: str
    lat: float
    lon: float


def infer_parents(
    dt_lat: float,
    dt_lon: float,
    poles: list[PoleNode],
    max_edge_m: float = 180.0,
) -> dict[str, str | None]:
    """Return pole_id -> parent_pole_id (None means directly under DT).

    Uses Prim's algorithm from a virtual DT root. If a pole cannot connect
    within max_edge_m to the growing tree, we still attach it to the nearest
    tree node (with degraded confidence later).
    """
    if not poles:
        return {}

    remaining = {p.id: p for p in poles}
    in_tree: dict[str, PoleNode] = {}
    parents: dict[str, str | None] = {}

    # Seed: attach nearest pole to DT
    first = min(poles, key=lambda p: haversine_m(dt_lat, dt_lon, p.lat, p.lon))
    parents[first.id] = None
    in_tree[first.id] = first
    del remaining[first.id]

    while remaining:
        best_pole_id = None
        best_parent_id: str | None = None
        best_dist = float("inf")

        for pid, pole in remaining.items():
            # Prefer connecting to a tree pole; also allow DT if closer
            d_dt = haversine_m(dt_lat, dt_lon, pole.lat, pole.lon)
            if d_dt < best_dist:
                best_dist = d_dt
                best_pole_id = pid
                best_parent_id = None

            for tid, tnode in in_tree.items():
                d = haversine_m(tnode.lat, tnode.lon, pole.lat, pole.lon)
                # Soft preference: poles closer to DT as parents when distances similar
                if d < best_dist:
                    best_dist = d
                    best_pole_id = pid
                    best_parent_id = tid

        assert best_pole_id is not None
        parents[best_pole_id] = best_parent_id
        in_tree[best_pole_id] = remaining[best_pole_id]
        del remaining[best_pole_id]

    return parents


def children_map(parents: dict[str, str | None]) -> dict[str | None, list[str]]:
    kids: dict[str | None, list[str]] = {}
    for child, parent in parents.items():
        kids.setdefault(parent, []).append(child)
    return kids


def descendants(root: str, kids: dict[str | None, list[str]]) -> list[str]:
    out: list[str] = []
    stack = list(kids.get(root, []))
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(kids.get(n, []))
    return out
