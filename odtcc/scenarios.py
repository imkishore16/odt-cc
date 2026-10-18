"""
ODT-CC — scenario engine.

A *plan* is a set of operator interventions. This module applies a plan to the
venue, runs the simulator, and scores the result, so "do nothing" and
"approve the plan" can be compared side by side.

Run:  python -m odtcc.scenarios          (GPU: python -m cudf.pandas -m odtcc.scenarios)
Out:  data/egress_<name>.csv for each scenario (plan approved at APPROVAL_MINUTE), plus a comparison table
"""
import copy
import json
import os

from odtcc.generate_data import load_venue, simulate
from odtcc.paths import DATA_DIR, SCENARIOS_FILE, egress_csv

# What an operator can change, and the limits of what's realistic.
#   stadium_release_per_min  - hold people in the bowl and release them gradually (None = no limit)
#   train_interval_min       - minutes between trains at the station (venue default: 4)
#   shuttle_outflow_per_min  - people/minute the shuttle buses can carry away (default: 150)
#   north_to_shuttle_share   - share of the north plaza crowd steered to shuttles instead of the station (default: 0.4)
#   east_to_south_share      - share of east-gate exits redirected to the south plaza (default: 0)
LIMITS = {
    "stadium_release_per_min": (800, 2500),
    "train_interval_min": (2, 4),
    "shuttle_outflow_per_min": (150, 450),
    "north_to_shuttle_share": (0.4, 0.8),
    "east_to_south_share": (0.0, 0.8),
}

DO_NOTHING = {}

# Lever values when the operator changes nothing (2500/min = the stadium's own exit capacity)
DEFAULTS = {
    "stadium_release_per_min": 2500,
    "train_interval_min": 4,
    "shuttle_outflow_per_min": 150,
    "north_to_shuttle_share": 0.4,
    "east_to_south_share": 0.0,
}

APPROVAL_MINUTE = 3  # demo story: alert at minute 2, operator approves at minute 3

BUS_PEOPLE_PER_MIN = 4  # one 60-seat bus on a ~15-minute round trip (planning assumption)

DEMO_PLAN = {
    "stadium_release_per_min": 1200,
    "train_interval_min": 3,
    "shuttle_outflow_per_min": 300,
    "north_to_shuttle_share": 0.55,
    "east_to_south_share": 0.5,
}


def load_scenarios():
    with open(SCENARIOS_FILE) as f:
        return json.load(f)


def venue_for(scenario_id="normal"):
    """The venue with that scenario's incidents attached (simulate() reads venue["incidents"])."""
    sc = load_scenarios()[scenario_id]
    venue = load_venue()
    venue.update(scenario=scenario_id, scenario_title=sc["title"],
                 scenario_description=sc["description"], incidents=sc["incidents"])
    return venue


INFLOW_LIMITS = (50, 1000)  # extra people/min an operator can add in a custom incident

# Planning-mode inputs: what the event will look like, before any operator lever
PLANNING_LIMITS = {
    "attendance": (40000, 80000),          # tickets sold
    "stagger_share": (0.0, 0.5),           # share of the crowd kept seated for a second wave
    "stagger_delay": (0, 30),              # minutes until the second wave leaves
    "nearby_event_people": (0, 20000),     # crowd of an overlapping session nearby (heads for the same station)
    "nearby_event_minute": (0, 40),        # when that session lets out, in our minutes
    "train_interval": (2, 8),              # planned E Line frequency at Expo Park/USC
    "train_capacity": (600, 1200),         # people per train
}
ROAD_CLOSURES = {   # no traffic model: a closure acts through the pedestrian/transit capacity it removes
    "close_vermont": {"type": "capacity", "node": "parking_exit", "factor": 0.4,
                      "label": "Vermont Ave closed: parking exits at 40% capacity"},
    "close_exposition": {"type": "capacity", "node": "shuttle_stop", "factor": 0.5,
                         "label": "Exposition Blvd closed: shuttles load at 50% capacity"},
}


def planning_venue(venue, planning):
    """The venue as planned: tickets sold, staggered exit, overlapping session, timetable, road closures."""
    for key, value in planning.items():
        if key in PLANNING_LIMITS:
            lo, hi = PLANNING_LIMITS[key]
            if not lo <= value <= hi:
                raise ValueError(f"{key}={value} outside {lo}-{hi}")
        elif key not in ROAD_CLOSURES:
            raise ValueError(f"unknown planning input {key!r}")
    v = copy.deepcopy(venue)
    notes = []
    if planning.get("attendance"):
        v["attendance"] = int(planning["attendance"])
        notes.append(f"{v['attendance']:,} tickets")
    share, delay = planning.get("stagger_share", 0), planning.get("stagger_delay", 0)
    if share and delay:
        v["release_waves"] = [(0, 1 - share), (int(delay), share)]
        notes.append(f"{share:.0%} held for a second wave after {int(delay)} min")
    platform = _node(v, "station_platform")
    if planning.get("train_interval"):
        platform["train_interval_min"] = int(planning["train_interval"])
    if planning.get("train_capacity"):
        platform["per_train"] = int(planning["train_capacity"])
    notes.append(f"trains every {platform['train_interval_min']} min x {platform['per_train']} people")
    incidents = list(v.get("incidents", []))
    if planning.get("nearby_event_people"):
        start = int(planning.get("nearby_event_minute", 0))
        incidents.append({"type": "inflow", "node": "walkway_station", "from": start, "to": start + 20,
                          "per_min": round(planning["nearby_event_people"] / 20)})
        notes.append(f"nearby event of {int(planning['nearby_event_people']):,} lets out at min {start}")
    for key, closure in ROAD_CLOSURES.items():
        if planning.get(key):
            incidents.append({k: closure[k] for k in ("type", "node", "factor")} | {"from": 0, "to": 120})
            notes.append(closure["label"])
    v["incidents"] = incidents
    v["planning_notes"] = notes
    return v


def custom_venue(incidents):
    """The venue with operator-built incidents, after checking each one is within range."""
    venue = load_venue()
    zones = {n["id"]: n["label"] for n in venue["nodes"] if "area_m2" in n}
    clean, parts = [], []
    for inc in incidents:
        kind, node = inc.get("type"), inc.get("node")
        if kind not in ("capacity", "inflow", "sensor_outage"):
            raise ValueError(f"unknown incident type {kind!r}")
        if node not in zones:
            raise ValueError(f"unknown zone {node!r}")
        start, end = int(inc["from"]), int(inc["to"])
        if not 0 <= start < end <= 120:
            raise ValueError(f"incident minutes {start}-{end} must satisfy 0 <= from < to <= 120")
        c = {"type": kind, "node": node, "from": start, "to": end}
        where = f"from minute {start} to {end}"
        if kind == "capacity":
            c["factor"] = float(inc["factor"])
            if not 0.0 <= c["factor"] <= 1.0:
                raise ValueError("capacity factor must be between 0 and 1")
            parts.append(f"{zones[node]} runs at {c['factor']:.0%} capacity {where}")
        elif kind == "inflow":
            c["per_min"] = int(inc["per_min"])
            if not INFLOW_LIMITS[0] <= c["per_min"] <= INFLOW_LIMITS[1]:
                raise ValueError(f"extra crowd must be {INFLOW_LIMITS[0]}-{INFLOW_LIMITS[1]} people/min")
            parts.append(f"{c['per_min']} extra people per minute join {zones[node]} {where}")
        else:
            parts.append(f"the sensors at {zones[node]} are offline {where}")
        clean.append(c)
    if not clean:
        raise ValueError("a custom scenario needs at least one incident")
    venue.update(scenario="custom", scenario_title="Custom incident",
                 scenario_description="Custom: " + "; ".join(parts) + ".", incidents=clean)
    return venue


def _node(venue, nid):
    return next(n for n in venue["nodes"] if n["id"] == nid)


def apply_plan(venue, plan):
    """Return a copy of the venue with the plan's interventions applied."""
    for key, value in plan.items():
        lo, hi = LIMITS[key]
        if not lo <= value <= hi:
            raise ValueError(f"{key}={value} outside realistic range {lo}-{hi}")

    v = copy.deepcopy(venue)
    if "train_interval_min" in plan:
        _node(v, "station_platform")["train_interval_min"] = plan["train_interval_min"]
    if "shuttle_outflow_per_min" in plan:
        _node(v, "shuttle_stop")["outflow_per_min"] = plan["shuttle_outflow_per_min"]
    if "north_to_shuttle_share" in plan:
        s = plan["north_to_shuttle_share"]
        _node(v, "plaza_N")["routes"] = {"walkway_station": 1 - s, "shuttle_stop": s}
    if "east_to_south_share" in plan:
        s = plan["east_to_south_share"]
        _node(v, "gate_E")["routes"] = {"plaza_N": 1 - s, "plaza_S": s}
    return v


def run(venue, plan, minutes=120, seed=42, start_minute=0, base_plans=()):
    """Simulate with the plan in force from start_minute (the approval minute).

    base_plans: earlier approvals [(minute, plan), ...] still in force before start_minute
    ("do nothing" before the first of them). Each approval replaces the previous lever set.
    """
    log = [(m, p) for m, p in base_plans if m < start_minute] + [(start_minute, plan)]
    return run_log(venue, log, minutes=minutes, seed=seed)


def run_log(venue, log, minutes=120, seed=42):
    """Simulate a whole approval log [(minute, plan), ...]; an empty log is "do nothing"."""
    phases = [(m, apply_plan(venue, p), p.get("stadium_release_per_min")) for m, p in log]
    return simulate(venue, minutes=minutes, seed=seed, phases=phases)


def score(df, from_minute=0):
    """Headline numbers an operator (and a judge) cares about.

    from_minute: count danger and peak only from this minute on (a decision can't change the past).
    """
    zones = df.dropna(subset=["density"])
    zones = zones[zones["minute"] >= from_minute]
    dangerous = zones[zones["level"] == "dangerous"]
    in_venue = df[df["node"] == "stadium"]
    cleared = in_venue[in_venue["occupancy"] == 0]["minute"]
    worst = zones.loc[zones["density"].idxmax()]
    return {
        "dangerous_zone_minutes": int(len(dangerous)),
        "zones_ever_dangerous": int(dangerous["node"].nunique()),
        "peak_density": float(worst["density"]),
        "peak_zone": str(worst["node"]),
        "people_minutes_in_danger": int(dangerous["occupancy"].sum()),
        "stadium_cleared_by_min": int(cleared.min()) if len(cleared) else None,
    }


def cost(plan):
    """How hard a plan is to execute.

    effort: sum over levers of how far each is pushed from DEFAULTS towards its limit
            (0 = do nothing, 1 per lever at its limit, 5 = everything maxed out).
    The other fields are the plain-language resources for the operator's action card.
    """
    lv = {**DEFAULTS, **plan}
    effort = 0.0
    for key, (lo, hi) in LIMITS.items():
        far_end = lo if DEFAULTS[key] == hi else hi  # direction the operator pushes the lever
        effort += abs(lv[key] - DEFAULTS[key]) / abs(far_end - DEFAULTS[key])
    extra_shuttle = lv["shuttle_outflow_per_min"] - DEFAULTS["shuttle_outflow_per_min"]
    return {
        "effort": round(effort, 2),
        "extra_trains_per_hour": round(60 / lv["train_interval_min"] - 60 / DEFAULTS["train_interval_min"], 1),
        "extra_shuttle_capacity_per_min": int(extra_shuttle),
        "extra_buses_est": int(round(extra_shuttle / BUS_PEOPLE_PER_MIN)),
        "holds_crowd_in_bowl": lv["stadium_release_per_min"] < DEFAULTS["stadium_release_per_min"],
        "redirects_crowd": lv["north_to_shuttle_share"] > DEFAULTS["north_to_shuttle_share"]
                           or lv["east_to_south_share"] > DEFAULTS["east_to_south_share"],
    }


def approval_deadline(venue, plan, from_minute=0, max_minute=60, **run_kwargs):
    """Last approval minute at which the plan still prevents every dangerous zone-minute.

    Returns None if the plan isn't safe even when approved at from_minute.
    run_kwargs may include base_plans (approvals already in force).
    """
    deadline = None
    for t in range(from_minute, max(max_minute, from_minute) + 1):
        if score(run(venue, plan, start_minute=t, **run_kwargs), from_minute)["dangerous_zone_minutes"] > 0:
            break
        deadline = t
    return deadline


def main():
    venue = load_venue()
    os.makedirs(DATA_DIR, exist_ok=True)
    results = {}
    for name, plan in {"baseline": DO_NOTHING, "plan": DEMO_PLAN}.items():
        df = run(venue, plan, start_minute=APPROVAL_MINUTE)
        df.to_csv(egress_csv(name), index=False)
        results[name] = score(df)

    with open(DATA_DIR / "scenario_scores.json", "w") as f:
        json.dump({"plans": {"baseline": DO_NOTHING, "plan": DEMO_PLAN},
                   "approval_minute": APPROVAL_MINUTE,
                   "plan_cost": cost(DEMO_PLAN),
                   "plan_approval_deadline": approval_deadline(venue, DEMO_PLAN),
                   "scores": results}, f, indent=2)

    keys = list(results["baseline"])
    print(f"Plan approved at minute {APPROVAL_MINUTE}")
    print(f"{'metric':<28}{'do nothing':>18}{'with plan':>18}")
    for k in keys:
        print(f"{k:<28}{str(results['baseline'][k]):>18}{str(results['plan'][k]):>18}")


if __name__ == "__main__":
    main()