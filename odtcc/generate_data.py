"""
ODT-CC — synthetic post-event egress data generator.

Reads venue.json (the single source of truth shared with the twin) and simulates
minute-by-minute crowd occupancy and density at every node after the final whistle.

Run:     python -m odtcc.generate_data            (from the project root)
Output:  data/egress_baseline.csv
On GPU:  python -m cudf.pandas -m odtcc.generate_data
"""
import json
import os
import random

import pandas as pd

from odtcc.paths import DATA_DIR, VENUE_FILE, egress_csv

# Rough crowd-safety bands (people per m²), loosely after Fruin levels of service
LEVELS = [(2.0, "normal"), (3.5, "crowded"), (5.0, "high"), (float("inf"), "dangerous")]
MAX_DENSITY = 6.0  # people/m² — nobody more can physically squeeze in; excess queues upstream


def load_venue(path=VENUE_FILE):
    with open(path) as f:
        return json.load(f)


def level_for(density):
    if density is None:
        return None
    for limit, name in LEVELS:
        if density < limit:
            return name


def capacity(node, minute, rng):
    """People who can leave this node during this minute."""
    if "train_interval_min" in node:  # platform empties in pulses when a train departs
        return node["per_train"] if minute % node["train_interval_min"] == 0 else 0
    return node["outflow_per_min"] * rng.uniform(0.85, 1.10)  # noise


def simulate(venue, minutes=120, seed=42, stadium_release_per_min=None,
             venue_after=None, switch_minute=0, phases=None):
    """stadium_release_per_min lets you test a staggered-exit intervention.

    Mid-run interventions (the operator's approvals): phases is a list of
    (start_minute, venue_variant, stadium_release_per_min). From each start minute the
    simulation uses that variant's capacities/routes and release cap, until the next phase;
    before the first, the original venue with no cap. Without phases, venue_after and
    switch_minute describe a single phase (the defaults put it in force from minute 0).
    Incidents (venue["incidents"]) only act from their own start minute, so re-simulating
    with a longer log reproduces every earlier minute exactly.
    """
    rng = random.Random(seed)
    incidents = venue.get("incidents", [])
    waves = venue.get("release_waves")   # [(start_minute, share of the crowd), ...] for a staggered session end
    if phases is None:
        phases = [(switch_minute, venue_after or venue, stadium_release_per_min)]
    phases = [(start, {n["id"]: n for n in v["nodes"]}, release)
              for start, v, release in sorted(phases, key=lambda p: p[0])]
    nodes_before = {n["id"]: n for n in venue["nodes"]}
    occ = {nid: 0.0 for nid in nodes_before}
    occ["stadium"] = float(venue["attendance"])
    rows = []

    for t in range(minutes):
        nodes, release = nodes_before, None
        for start, phase_nodes, phase_release in phases:
            if t >= start:
                nodes, release = phase_nodes, phase_release
        outflow = {}
        for nid, node in nodes.items():
            cap = capacity(node, t, rng)
            for inc in incidents:
                if inc["type"] == "capacity" and inc["node"] == nid and inc["from"] <= t < inc["to"]:
                    cap *= inc["factor"]
            if nid == "stadium" and release:
                cap = min(cap, release)
            if nid == "stadium" and waves:  # staggered session end: later waves are still seated
                held = venue["attendance"] * sum(share for start, share in waves if start > t)
                cap = min(cap, max(0.0, occ[nid] - held))
            outflow[nid] = max(0.0, min(occ[nid], cap))

        # People can only move into space that exists downstream: a full node blocks
        # its senders, so queues propagate backwards like real crowds do.
        wanted = {nid: 0.0 for nid in nodes}
        for nid, out in outflow.items():
            for dest, share in nodes[nid].get("routes", {}).items():
                wanted[dest] += out * share
        scale = {}
        for nid, node in nodes.items():
            area = node.get("area_m2")
            if not area or wanted[nid] <= 1e-9:
                scale[nid] = 1.0
                continue
            space = max(area * MAX_DENSITY - (occ[nid] - outflow[nid]), 0.0)
            scale[nid] = min(1.0, space / wanted[nid])

        inflow = {nid: 0.0 for nid in nodes}
        blocked = {nid: 0.0 for nid in nodes}
        for nid, out in outflow.items():
            routes = nodes[nid].get("routes", {})
            moved = 0.0
            for dest, share in routes.items():
                flow = out * share * scale[dest]
                inflow[dest] += flow
                moved += flow
            if routes:  # nodes with no routes are exits: everyone leaves the system
                blocked[nid] = out - moved
                outflow[nid] = moved
            occ[nid] -= outflow[nid]
        for nid in nodes:
            occ[nid] += inflow[nid]
        # Crowds from outside the venue (e.g. another event letting out) join a zone if there is room
        for inc in incidents:
            if inc["type"] == "inflow" and inc["from"] <= t < inc["to"]:
                area = nodes[inc["node"]]["area_m2"]
                extra = min(inc["per_min"], max(area * MAX_DENSITY - occ[inc["node"]], 0.0))
                occ[inc["node"]] += extra
                inflow[inc["node"]] += extra

        for nid, node in nodes.items():
            area = node.get("area_m2")
            density = round(occ[nid] / area, 2) if area else None
            rows.append({
                "minute": t,
                "node": nid,
                "label": node["label"],
                "type": node["type"],
                "occupancy": round(occ[nid]),
                "inflow": round(inflow[nid]),
                "outflow": round(outflow[nid]),
                "blocked": round(blocked[nid]),  # wanted to move on but no room downstream
                "density": density,
                "level": level_for(density),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    venue = load_venue()
    df = simulate(venue)
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(egress_csv("baseline"), index=False)

    peaks = (df.dropna(subset=["density"])
               .sort_values("density", ascending=False)
               .groupby("node").head(1)
               .sort_values("density", ascending=False))
    print("Peak density per node (baseline, no intervention):")
    print(peaks[["node", "minute", "occupancy", "density", "level"]].to_string(index=False))
