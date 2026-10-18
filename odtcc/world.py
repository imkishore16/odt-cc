"""
ODT-CC — world agent: the hidden reality of a live run.

During playback it may strike with a realistic incident at any minute in its window. Nobody
scripts when or what: kind, place, size and real duration are drawn from the catalog below.
The command centre never sees the truth. It gets the *report* a real control room would get
(a Metro alert, an LAFD call, a dead sensor) and plans with the *typical* duration; the
real duration stays hidden and only shows up in the twin and the sensor data.
"""
import random

ZONES = ["gate_N", "gate_E", "gate_S", "gate_W", "plaza_N", "plaza_S",
         "walkway_station", "station_platform", "shuttle_stop", "parking_exit"]

# weight = relative odds; ranges are (low, high) drawn uniformly; typical = what the report says
CATALOG = {
    "rail_signal_failure": dict(
        weight=3, type="capacity", nodes=["station_platform"], factor=(0.0, 0.0), duration=(15, 35), typical=15,
        source="LA Metro alert (GTFS-realtime)",
        report="E Line signal problem near Expo Park/USC: trains held, typically about {typical} minutes."),
    "rail_reduced_service": dict(
        weight=2, type="capacity", nodes=["station_platform"], factor=(0.3, 0.5), duration=(15, 30), typical=20,
        source="LA Metro alert (GTFS-realtime)",
        report="E Line short of trains: Expo Park/USC at about {pct}% of normal capacity for about {typical} minutes."),
    "medical_walkway": dict(
        weight=2, type="capacity", nodes=["walkway_station"], factor=(0.3, 0.5), duration=(6, 15), typical=10,
        source="LAFD dispatch",
        report="Medical emergency on the walkway to Expo Park/USC: lanes closed, about {pct}% capacity "
               "for about {typical} minutes."),
    "nearby_event": dict(
        weight=3, type="inflow", nodes=["walkway_station", "walkway_station", "plaza_N"], per_min=(400, 900), duration=(12, 25),
        typical=20, source="Exposition Park venue operations",
        venues=["BMO Stadium", "the Natural History Museum", "the California Science Center"],
        report="{venue} is letting out early: about {per_min} extra people per minute heading into {zone} "
               "for about {typical} minutes."),
    "shuttle_breakdown": dict(
        weight=2, type="capacity", nodes=["shuttle_stop"], factor=(0.2, 0.5), duration=(15, 30), typical=20,
        source="LA28 Transport",
        report="Several shuttle buses out of service: loading at about {pct}% capacity for about {typical} minutes."),
    "sensor_failure": dict(
        weight=1, type="sensor_outage", nodes=ZONES, duration=(10, 40), typical=20,
        source="Network operations",
        report="Bluetooth counters at {zone} stopped reporting."),
}


class WorldAgent:
    def __init__(self, labels, seed=None, window=(5, 45), max_incidents=2, min_gap=8, hazard=0.08):
        self.seed = seed if seed is not None else random.randrange(10**6)
        self.rng = random.Random(self.seed)
        self.labels = labels
        self.window, self.max_incidents, self.min_gap, self.hazard = window, max_incidents, min_gap, hazard
        self.events = []        # [{id, minute, kind, source, report, truth, belief}]
        self.last_tick = -1

    def tick(self, minute):
        """Called as playback reaches a new minute. May return a new incident starting now."""
        if minute <= self.last_tick:
            return None
        self.last_tick = minute
        lo, hi = self.window
        recent = self.events and minute - self.events[-1]["minute"] < self.min_gap
        if lo <= minute <= hi and len(self.events) < self.max_incidents and not recent \
                and self.rng.random() < self.hazard:
            return self.inject(minute)
        return None

    def inject(self, minute, kind=None, reported=None):
        """Strike now (also the demo's "Inject incident now" button).

        reported=False: nobody tells the command centre (about half of nearby-event crowds); it has to
        notice the build-up in the sensor data itself.
        """
        active = {e["kind"] for e in self.events if e["truth"]["to"] > minute}
        kinds = [k for k in CATALOG if k not in active] or list(CATALOG)   # no second copy of an ongoing incident
        kind = kind or self.rng.choices(kinds, weights=[CATALOG[k]["weight"] for k in kinds])[0]
        c = CATALOG[kind]
        node = self.rng.choice(c["nodes"])
        duration = self.rng.randint(*c["duration"])
        eid = len(self.events) + 1
        truth = {"id": eid, "type": c["type"], "node": node, "from": minute, "to": minute + duration}
        fill = {"typical": c["typical"], "zone": self.labels[node]}
        if c["type"] == "capacity":
            truth["factor"] = round(self.rng.uniform(*c["factor"]), 2)
            fill["pct"] = round(truth["factor"] * 100)
        elif c["type"] == "inflow":
            truth["per_min"] = self.rng.randrange(c["per_min"][0], c["per_min"][1] + 1, 50)
            fill.update(per_min=truth["per_min"], venue=self.rng.choice(c["venues"]))
        # What the command centre plans with: the same incident, but the *typical* duration
        belief = dict(truth, to=minute + c["typical"])
        report = c["report"].format(**fill)
        if reported is None:
            reported = not (kind == "nearby_event" and self.rng.random() < 0.5)
        event = {"id": eid, "minute": minute, "kind": kind, "source": c["source"], "status": "new",
                 "report": report[0].upper() + report[1:], "truth": truth, "belief": belief,
                 "reported": reported}
        self.events.append(event)
        return event

    def updates(self, minute):
        """Follow-up reports, as a real control room gets them: still ongoing past the expected end
        (the belief is extended by 5 minutes), or cleared earlier than expected (belief ends now)."""
        out = []
        for e in self.events:
            if not e["reported"]:
                continue
            truth, belief = e["truth"], e["belief"]
            if minute == belief["to"] and truth["to"] > minute:
                belief["to"] = minute + 5
                out.append({"id": e["id"], "minute": minute, "kind": e["kind"], "source": e["source"],
                            "status": "still ongoing", "belief": dict(belief),
                            "report": f"Update: still not resolved at minute {minute}; now expected "
                                      f"until about minute {belief['to']}."})
            elif minute == truth["to"] and belief["to"] > minute:
                belief["to"] = minute
                out.append({"id": e["id"], "minute": minute, "kind": e["kind"], "source": e["source"],
                            "status": "cleared", "belief": dict(belief),
                            "report": f"Update: cleared at minute {minute}."})
        return out
