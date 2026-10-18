"""
ODT-CC — brain service.

FastAPI on port 8000. The Kit extension calls this on every timeline tick, on each alert or
reported incident, and when the operator approves, resets or switches scenario.

Two worlds are kept apart:
  truth   what really happens (incidents with their real durations): drives the 3D twin and the sensors
  belief  what the command centre knows (reported incidents with typical durations): drives the
          forecast, the optimizer, the agent and every number the operator sees
In a live run the world agent (odtcc/world.py) adds incidents at random as playback reaches each minute.

Run:
    uvicorn odtcc.server:app --port 8000 --host 0.0.0.0

Endpoints:
    GET  /health                 server ready check
    GET  /scenarios              preset scenarios (+ "live"), zones, limits
    POST /scenario               switch scenario: preset id, "live" (random incidents) or "custom"
    GET  /state?minute=t         per-zone estimate + 15-min forecast + alert band + incident reports
    POST /recommend              optimizer sweep on top of the plans in force -> Nemotron action card
    GET  /levers                 slider definitions for the operator's own plan
    POST /simulate               score an operator-proposed plan
    POST /approve                add a plan to the approval log, rebuild crowd_live.usda
    POST /incident/inject        live run: the world agent strikes now (demo control)
    POST /reset                  back to minute 0, nothing approved (live: a fresh random world)
"""
import asyncio
import copy
import json
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from odtcc.agent import AGENCY, Facts, describe_levers, recommend as agent_recommend
from odtcc.build_twin import CROWD_LIVE_FILE, TWIN_LIVE_FILE, build_crowd_layer, build_live_twin
from odtcc.paths import DATA_DIR, ROOT
from odtcc.predictor import ALERT_AT, DANGER, Forecaster
from odtcc.scenarios import (DEFAULTS, DO_NOTHING, INFLOW_LIMITS, LIMITS, apply_plan, approval_deadline, cost,
                             custom_venue, load_scenarios, planning_venue, run, run_log, score, venue_for)
from odtcc.sensors import SMOOTH_MINUTES, observe, sensor_summary
from odtcc.world import CATALOG, WorldAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("odtcc.server")

app = FastAPI(title="ODT-CC Brain Service", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="odtcc")

LIVE_TITLE = "Live: random incidents"
LIVE_DESCRIPTION = ("Normal egress, but the world agent may cause realistic incidents at random from minute 5 "
                    "(nothing is scripted). The command centre only gets the reports.")

# ------------------------------------------------------------------ server state
_state: Dict[str, Any] = {
    "venue": None,        # belief: what the command centre knows
    "truth": None,        # reality: drives the twin and the sensors
    "labels": None,       # {node_id: label}
    "forecaster": None,
    "plans": [],          # approval log [(minute, plan), ...]
    "world": None,        # WorldAgent in the live scenario
    "live_sim": None,     # the truth, simulated with the approval log
    "live_obs": None,     # what the sensors report about live_sim; /state and the forecast use this
    "feeds": None,        # ticket scans, train departures, outages
    "last_card": None,    # newest recommendation (shared by the Kit panel and the web dashboard)
    "card_seq": 0,        # id of the newest card
    "generation": 0,      # +1 on every reset/scenario switch: a card computed before it is stale
    "clock": {"minute": 0, "source": None},   # the minute the driving client (Kit, or a standalone dashboard) is at
    "kit_seen": 0.0,      # wall time of Kit's last /state call
    "commands": [],       # queued for Kit: the dashboard drives Kit's timeline through these
}
KIT_ALIVE_S = 3.0
_lock = threading.RLock()

AUDIT_FILE = DATA_DIR / "audit_log.jsonl"
_audit_tail = deque(maxlen=50)   # newest decisions, also returned by /state
_audit_seq = [0]                 # entries written so far (invalidates the /state cache)


def _audit(kind, minute, text, **data):
    """Decision log / audit trail: every scenario switch, incident, recommendation and operator decision."""
    venue = _state.get("venue") or {}
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, "minute": minute,
             "scenario": venue.get("scenario"), "text": text, **data}
    _audit_tail.append(entry)
    _audit_seq[0] += 1
    with open(AUDIT_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ------------------------------------------------------------------ startup and world changes
@app.on_event("startup")
async def startup():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _startup_sync)


def _startup_sync():
    log.info("Loading forecaster…")
    _state["forecaster"] = Forecaster()
    os.makedirs(DATA_DIR, exist_ok=True)
    _load_scenario_sync("normal")
    log.info("Server ready — crowd_live.usda seeded with baseline data.")


def _load_scenario_sync(scenario_id, venue=None, seed=None):
    """Switch scenario: clear approvals, (live) start a fresh world agent, rebuild the live layer."""
    world = None
    if scenario_id == "live":
        venue = venue_for("normal")
        venue.update(scenario="live", scenario_title=LIVE_TITLE, scenario_description=LIVE_DESCRIPTION,
                     incidents=[], reports=[])
    venue = venue or venue_for(scenario_id)
    labels = {n["id"]: n["label"] for n in venue["nodes"]}
    if scenario_id == "live":
        world = WorldAgent(labels, seed=seed)
        log.info("Live run: world agent seed %d", world.seed)
    with _lock:
        _state.update(venue=venue, truth=copy.deepcopy(venue), labels=labels, plans=[], world=world, last_card=None,
                      detections={}, generation=_state["generation"] + 1)
        _rebuild_live()
    _audit("scenario", 0, f"Scenario: {venue['scenario_title']}" + (f" (random world {world.seed})" if world else ""))


def _rebuild_live():
    """Re-simulate the truth with the approval log and rewrite the live crowd layer. Call with _lock held."""
    sim = run_log(_state["truth"], _state["plans"])
    if TWIN_LIVE_FILE.exists():
        # Kit may have twin_live.usda open; rewriting it makes Kit ask to "fetch changes"
        build_crowd_layer(_state["truth"], sim, CROWD_LIVE_FILE)
    else:
        build_live_twin(_state["truth"], sim)
    _state["live_sim"] = sim
    _state["live_obs"], _state["feeds"] = observe(sim, _state["truth"])


def _apply_world_event(event):
    """A new incident: the truth gets the real one, the command centre gets the report. Call with _lock held."""
    log.info("World agent, minute %d: %s (truth %s)", event["minute"], event["report"], event["truth"])
    truth, venue = _state["truth"], _state["venue"]
    # new dicts rather than mutation: agent threads may be reading the old ones
    _state["truth"] = {**truth, "incidents": truth["incidents"] + [event["truth"]]}
    if event["reported"]:
        _state["venue"] = _with_report(venue, event, venue["incidents"] + [dict(event["belief"])])
    _rebuild_live()
    if event["reported"]:
        _audit("incident", event["minute"], f"{event['source']}: {event['report']}", incident_id=event["id"])
    else:
        log.info("  (unreported: the command centre has to detect it from the sensors)")


DETECT_MINUTES = 3        # consecutive minutes above expected before raising a detection
DETECT_COOLDOWN = 15      # no second detection in the same zone within this many minutes


def _detect(minute):
    """Fast detection of unreported incidents. Compares each zone's sensor estimate with what the command
    centre expects from its own picture (reported incidents + plans in force); a build-up well above that for
    DETECT_MINUTES in a row becomes a detection, added to the picture as an assumed extra inflow. Call with _lock held."""
    venue, obs = _state["venue"], _state["live_obs"]
    if minute < DETECT_MINUTES:
        return
    # compare like with like: the sensor estimate is a 3-minute trailing average, so smooth the expectation the
    # same way (otherwise a plan change that empties a zone quickly looks like an unexplained build-up)
    expected = (run_log(venue, _state["plans"]).pivot(index="minute", columns="node", values="density")
                .rolling(SMOOTH_MINUTES, min_periods=1).mean())
    seen = obs.pivot(index="minute", columns="node", values="density")
    status = obs.pivot(index="minute", columns="node", values="sensor")
    recent = _state.setdefault("detections", {})
    for node in [n for n in venue["nodes"] if "area_m2" in n]:
        nid = node["id"]
        if minute - recent.get(nid, -99) < DETECT_COOLDOWN:
            continue
        window = range(minute - DETECT_MINUTES + 1, minute + 1)
        if any(status.at[m, nid] != "ok" for m in window):
            continue
        gaps = [seen.at[m, nid] - expected.at[m, nid] for m in window]
        if not all(g > max(0.5, 0.3 * expected.at[m, nid]) for g, m in zip(gaps, window)):
            continue
        extra_people = gaps[-1] * node["area_m2"]
        per_min = int(min(900, max(150, round(extra_people / (DETECT_MINUTES + 1) / 50) * 50)))
        start = minute - DETECT_MINUTES
        recent[nid] = minute
        n = len(recent)
        item = {"id": f"d{n}", "minute": minute, "kind": "anomaly", "status": "new",
                "source": "ODT-CC anomaly detection (sensors)",
                "report": (f"Unreported crowd build-up at {node['label']}: {gaps[-1]:.1f} people/m² above expected "
                           f"for {DETECT_MINUTES} minutes. Treating it as about {per_min} extra people/min "
                           f"since minute {start} until it's explained.")}
        belief = {"id": f"d{n}", "type": "inflow", "node": nid, "per_min": per_min, "from": start, "to": start + 15}
        _state["venue"] = venue = _with_report(venue, item, venue["incidents"] + [belief])
        log.info("Detection at minute %d: %s", minute, item["report"])
        _audit("detection", minute, f"{item['source']}: {item['report']}")


def _apply_world_update(update):
    """A follow-up report (still ongoing / cleared): only the command centre's belief changes. Call with _lock held."""
    log.info("World agent update, minute %d: incident %d %s", update["minute"], update["id"], update["status"])
    venue = _state["venue"]
    incidents = [dict(update["belief"]) if i.get("id") == update["id"] else i for i in venue["incidents"]]
    _state["venue"] = _with_report(venue, update, incidents)
    _audit("incident_update", update["minute"], f"{update['source']}: {update['report']}", incident_id=update["id"])


def _with_report(venue, item, incidents):
    reports = venue.get("reports", []) + [{k: item[k] for k in ("id", "minute", "kind", "source", "status", "report")}]
    return {**venue, "incidents": incidents, "reports": reports,
            "scenario_description": " ".join(f"[minute {r['minute']}] {r['report']}" for r in reports)}


# ------------------------------------------------------------------ helpers
def _alert_band(predicted_density: float) -> str:
    if predicted_density >= DANGER:
        return "danger"
    if predicted_density >= ALERT_AT:
        return "caution"
    return "ok"


def _snapshot():
    with _lock:
        if _state["venue"] is None:
            raise HTTPException(503, "Server is still starting up")
        return dict(_state)


_state_cache = {}   # (minute, world version) -> /state payload; heartbeats and dashboard polls reuse it


def _state_response(minute: int) -> dict:
    """The /state payload, from the sensor feeds (not the simulator's truth). Cached until anything changes."""
    s = _snapshot()
    key = (minute, id(s["venue"]), id(s["live_obs"]), len(s["plans"]), s["generation"], _audit_seq[0])
    cached = _state_cache.get(key)
    if cached is not None:
        return dict(cached)
    if len(_state_cache) > 256:
        _state_cache.clear()
    response = _build_state(minute, s)
    _state_cache[key] = response
    return dict(response)


def _build_state(minute, s):
    venue, labels, forecaster, live_obs, plans = s["venue"], s["labels"], s["forecaster"], s["live_obs"], s["plans"]

    at_t = live_obs[live_obs["minute"] == minute]
    zone_rows = {row.node: row for row in at_t.itertuples(index=False)}

    # Forecaster: predict t+15 for all zones from the sensor estimates, with the plan now in force
    in_force = [p for p in plans if p[0] <= minute]
    start_min, plan_in_force = in_force[-1] if in_force else (0, DO_NOTHING)
    forecasts = forecaster.forecast(live_obs[live_obs["minute"] <= minute], plan_in_force, start_min)
    forecast_by_zone = {f["zone"]: f for f in forecasts}

    # Confidence: the forecaster's held-out error (larger when a zone is busy), worse when sensors miss or die
    test = forecaster.meta["test"]
    zones = []
    for node in [n for n in venue["nodes"] if "area_m2" in n]:
        nid = node["id"]
        zrow = zone_rows[nid]
        offline = zrow.sensor == "offline"
        pred = forecast_by_zone[nid]["predicted_density"]
        busy = pred >= 2 or (not offline and zrow.density >= 2)
        band = test["mae_busy"] if busy else test["mae"]
        confidence = "low" if offline else "medium" if zrow.sensor == "missed" else "high"
        band *= {"high": 1.0, "medium": 1.5, "low": 2.0}[confidence]
        zones.append({
            "id": nid,
            "label": labels[nid],
            "sensor": zrow.sensor,                       # ok | missed | offline
            "density_now": None if offline else round(float(zrow.density), 2),
            "occupancy_now": None if offline else int(zrow.occupancy),
            "level_now": "no data" if offline else str(zrow.level),
            "predicted_density": round(float(pred), 2),
            "predicted_minute": minute + forecaster.meta["horizon_min"],
            "alert_band": _alert_band(pred),
            "confidence": confidence,
            "error_band": round(band, 2),          # +- people/m² on the 15-min prediction
        })

    alerts = [z for z in zones if z["alert_band"] in ("caution", "danger")]
    incidents = [{**inc, "label": labels[inc["node"]],
                  "status": "active" if inc["from"] <= minute < inc["to"] else
                            "upcoming" if minute < inc["from"] else "over"}
                 for inc in venue.get("incidents", [])]
    return {
        "minute": minute,
        "scenario": {"id": venue["scenario"], "title": venue["scenario_title"],
                     "description": venue["scenario_description"]},
        "incidents": incidents,
        "reports": [r for r in venue.get("reports", []) if r["minute"] <= minute],
        "live": s["world"] is not None,
        "sensors": sensor_summary(live_obs, s["feeds"], minute),
        "plans_in_force": [[m, p] for m, p in in_force],
        "approved_plan": plan_in_force if in_force else None,
        "approved_minute": start_min if in_force else None,
        "zones": zones,
        "alerts": alerts,
        "audit": list(_audit_tail)[-8:],
    }


# ------------------------------------------------------------------ endpoints
@app.get("/health")
def health():
    return {"status": "ready" if _state["venue"] is not None else "starting"}


@app.get("/scenarios")
def scenarios():
    venue = _state["venue"]
    presets = [{"id": k, "title": v["title"], "description": v["description"]} for k, v in load_scenarios().items()]
    return {"current": venue["scenario"] if venue else None,
            "current_title": venue["scenario_title"] if venue else None,
            "current_description": venue["scenario_description"] if venue else None,
            "scenarios": presets[:1] + [{"id": "live", "title": LIVE_TITLE, "description": LIVE_DESCRIPTION}]
                         + presets[1:],
            "zones": [{"id": n["id"], "label": n["label"]} for n in venue_for()["nodes"] if "area_m2" in n],
            "inflow_limits": list(INFLOW_LIMITS),
            "incident_kinds": list(CATALOG) + ["nearby_event_unreported"]}


class ScenarioRequest(BaseModel):
    id: str
    incidents: Optional[List[Dict[str, Any]]] = None   # only for id == "custom"
    seed: Optional[int] = None                          # only for id == "live": replay a given random world
    planning: Optional[Dict[str, Any]] = None           # only for id == "planned": the event as planned


@app.post("/scenario")
async def set_scenario(req: ScenarioRequest):
    """Switch scenario (also clears approvals); the panel reloads crowd_live.usda afterwards.
    "custom" takes operator-built incidents; "live" starts a world agent that causes random incidents."""
    venue = None
    if req.id == "planned":
        try:
            venue = planning_venue(venue_for("normal"), req.planning or {})
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e))
        venue.update(scenario="planned", scenario_title="Planned event",
                     scenario_description="Planned event: " + "; ".join(venue["planning_notes"]) + ".")
    elif req.id == "custom":
        try:
            venue = custom_venue(req.incidents or [])
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(422, str(e))
    elif req.id not in ("live", "planned") and req.id not in load_scenarios():
        raise HTTPException(404, f"unknown scenario {req.id!r}")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, lambda: _load_scenario_sync(req.id, venue, req.seed))
    world = _state["world"]
    return {"status": "ok", "scenario": req.id, "description": _state["venue"]["scenario_description"],
            "seed": world.seed if world else None}


@app.get("/state")
def state(minute: int = Query(..., ge=0, le=200, description="Match-clock minute (0 = final whistle)"),
          client: str = Query("kit", description="kit (the Kit panel) or dashboard (standalone web dashboard)")):
    with _lock:
        _state["clock"] = {"minute": minute, "source": client}
        commands = []
        if client == "kit":
            _state["kit_seen"] = time.time()
            commands, _state["commands"] = _state["commands"], []
        world = _state["world"]
        if world is not None and minute > world.last_tick:
            previous = world.last_tick
            event = world.tick(minute)
            if event:
                _apply_world_event(event)
            for m in range(previous + 1, minute + 1):   # the panel may skip minutes while a request is in flight
                for update in world.updates(m):
                    _apply_world_update(update)
            _detect(minute)
    response = _state_response(minute)
    response["card_id"] = (_state["last_card"] or {}).get("card_id")
    if client == "kit":
        response["commands"] = commands
    return response


def _kit_alive():
    return time.time() - _state["kit_seen"] < KIT_ALIVE_S


@app.get("/snapshot")
def snapshot():
    """Everything the web dashboard shows, at the minute the driving client is at (no world tick here)."""
    clock = dict(_state["clock"])
    return {"clock": {**clock, "kit_alive": _kit_alive()},
            "state": _state_response(clock["minute"]),
            "card": _state["last_card"]}


@app.get("/card")
def card():
    if not _state["last_card"]:
        raise HTTPException(404, "no recommendation yet")
    return _state["last_card"]


class CommandRequest(BaseModel):
    action: str                                   # play | pause | reset | scenario | approved
    id: Optional[str] = None                      # scenario id
    incidents: Optional[List[Dict[str, Any]]] = None
    planning: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None         # "approved": the /approve response, so Kit shows the same result


@app.post("/command")
def command(req: CommandRequest):
    """Queue a command for the Kit panel (the dashboard drives Kit's timeline this way)."""
    if req.action not in ("play", "pause", "reset", "scenario", "approved"):
        raise HTTPException(422, f"unknown action {req.action!r}")
    if not _kit_alive():
        raise HTTPException(409, "Kit is not connected; use the dashboard's standalone clock")
    with _lock:
        _state["commands"].append(req.dict(exclude_none=True))
    return {"queued": req.action}


class InjectRequest(BaseModel):
    minute: int
    kind: Optional[str] = None     # a catalog kind, or "<kind>_unreported"; random when omitted


@app.post("/incident/inject")
async def inject(req: InjectRequest):
    """Live run only: make the world agent strike now (the demo's "Inject incident" button)."""
    kind, reported = req.kind, None
    if kind and kind.endswith("_unreported"):
        kind, reported = kind[:-len("_unreported")], False
    if kind is not None and kind not in CATALOG:
        raise HTTPException(422, f"unknown incident kind {req.kind!r}")

    def _inject():
        with _lock:
            world = _state["world"]
            if world is None:
                raise HTTPException(409, "incidents can only be injected in the live scenario")
            event = world.inject(req.minute, kind, reported)
            _apply_world_event(event)
            if not event["reported"]:   # the operator who pressed the button knows, the command centre doesn't
                return {"id": event["id"], "minute": event["minute"], "kind": event["kind"], "status": "unreported",
                        "source": "world agent", "report": "Unreported incident injected: watch for detection."}
            return {k: event[k] for k in ("id", "minute", "kind", "source", "status", "report")}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _inject)


class RecommendRequest(BaseModel):
    minute: int


@app.post("/recommend")
async def recommend(req: RecommendRequest):
    """Optimizer + Nemotron on top of the plans already in force; returns the action card.
    Blocking work (optimizer ~2 s, NIM ~2-4 s) runs in a thread pool so the event loop never stalls."""
    s = _snapshot()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor, lambda: _recommend_sync(req.minute, s["venue"], s["forecaster"], s["plans"], s["generation"]))


def _recommend_sync(minute, venue, forecaster, plans, generation):
    facts = Facts(minute, venue=venue, forecaster=forecaster, base_plans=plans)
    card = agent_recommend(minute, facts=facts, log=lambda msg: log.info("agent minute=%d: %s", minute, msg))
    # before/after aliases for the panel (the card already has do_nothing + expected)
    card["before"] = card.get("do_nothing", {})
    card["after"] = card.get("expected", {})
    with _lock:
        if generation != _state["generation"]:
            # the run was reset or switched while this card was computed: never let it reach the new run
            card["stale"] = True
            return card
        _state["card_seq"] += 1
        card["card_id"] = _state["card_seq"]
        _state["last_card"] = card
    scores = {k: o["score_0_to_100"] for k, o in card["options"].items()}
    _audit("recommendation", minute,
           f"{card['source']} recommends {card['recommended'].replace('_', ' ')}"
           + (" (no change needed)" if card.get("no_change") else "")
           + " | scores " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in scores.items()),
           source=card["source"], recommended=card["recommended"], scores=scores)
    return card


LEVER_LABELS = {
    "stadium_release_per_min": "Release from bowl (people/min)",
    "train_interval_min": "Train every (min)",
    "shuttle_outflow_per_min": "Shuttle capacity (people/min)",
    "north_to_shuttle_share": "North plaza to shuttles (share)",
    "east_to_south_share": "East gates to south (share)",
}


@app.get("/levers")
def levers():
    """Slider definitions for the operator's own plan."""
    return [{"key": k, "label": LEVER_LABELS[k], "agency": AGENCY[k], "min": lo, "max": hi,
             "default": DEFAULTS[k], "integer": isinstance(DEFAULTS[k], int)}
            for k, (lo, hi) in LIMITS.items()]


class SimulateRequest(BaseModel):
    plan: Dict[str, Any]
    minute: int


@app.post("/simulate")
async def simulate(req: SimulateRequest):
    """Score an operator-proposed plan approved at req.minute (on top of the plans in force)."""
    s = _snapshot()
    plan = {k: v for k, v in req.plan.items() if v != DEFAULTS.get(k)}
    try:
        apply_plan(s["venue"], plan)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor, lambda: _simulate_sync(plan, req.minute, s["venue"], s["labels"], s["plans"]))


def _simulate_sync(plan, minute, venue, labels, plans):
    base = [p for p in plans if p[0] < minute]
    df = run(venue, plan, start_minute=minute, base_plans=base)
    s = score(df, from_minute=minute)
    danger = df[(df["level"] == "dangerous") & (df["minute"] >= minute)].sort_values("minute")
    if len(danger):
        first = danger.iloc[0]
        verdict = "UNSAFE"
        first_danger = {"zone": first["node"], "label": labels[first["node"]], "minute": int(first["minute"])}
    else:
        verdict = "MARGINAL" if s["peak_density"] >= ALERT_AT else "SAFE"
        first_danger = None
    return {
        "minute": minute,
        "plan": plan,
        "actions": describe_levers(plan),
        "verdict": verdict,
        "score": s,
        "peak_zone_label": labels[s["peak_zone"]],
        "first_danger": first_danger,
        "cost": cost(plan),
        "approve_by_minute": (approval_deadline(venue, plan, from_minute=minute, base_plans=base)
                              if verdict != "UNSAFE" else None),
        "baseline_label": "keeping the current plan" if base else "doing nothing",
        "baseline_score": score(run_log(venue, base), from_minute=minute),
    }


ATTENDANCE_LIMITS = (40000, 80000)   # planning input: tickets sold


class ComparePlan(BaseModel):
    name: str
    plan: Dict[str, Any]


class CompareRequest(BaseModel):
    plans: List[ComparePlan]
    minute: int = 0
    attendance: Optional[int] = None
    planning: Optional[Dict[str, Any]] = None   # planning inputs (see scenarios.PLANNING_LIMITS / ROAD_CLOSURES)


@app.post("/compare")
async def compare(req: CompareRequest):
    """Planning mode: score several plans side by side (plus the "no change" baseline), optionally for a
    different number of tickets sold. Plans start at req.minute, on top of the plans already in force."""
    s = _snapshot()
    planning = dict(req.planning or {})
    if req.attendance is not None:
        planning["attendance"] = req.attendance
    try:
        venue = planning_venue(s["venue"], planning)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    timetable = next(n for n in venue["nodes"] if n["id"] == "station_platform")["train_interval_min"]
    plans = []
    for p in req.plans:
        # keep an explicit train frequency when it differs from the planned timetable (not just from the default)
        plan = {k: v for k, v in p.plan.items()
                if (v != timetable if k == "train_interval_min" else v != DEFAULTS.get(k))}
        try:
            apply_plan(venue, plan)
        except (ValueError, KeyError) as e:
            raise HTTPException(422, f"{p.name}: {e}")
        plans.append((p.name, plan))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor, lambda: _compare_sync(plans, req.minute, venue, s["labels"], s["plans"]))


def _compare_sync(plans, minute, venue, labels, approvals):
    base = [p for p in approvals if p[0] < minute]
    rows = [_plan_row("Keep current plan" if base else "Do nothing", None, minute, venue, labels, base)]
    rows += [_plan_row(name, plan, minute, venue, labels, base) for name, plan in plans]
    _audit("compare", minute, f"Compared plans for {venue['attendance']:,} tickets: "
           + ", ".join(f"{r['name']} {r['verdict']}" for r in rows))
    return {"minute": minute, "attendance": venue["attendance"], "planning_notes": venue.get("planning_notes", []),
            "rows": rows}


def _plan_row(name, plan, minute, venue, labels, base):
    """One column of the comparison table. plan=None is the baseline (whatever is in force)."""
    df = run(venue, plan, start_minute=minute, base_plans=base) if plan is not None else run_log(venue, base)
    s = score(df, from_minute=minute)
    zones = df[(df["minute"] >= minute) & df["density"].notna()]
    in_force = plan if plan is not None else (base[-1][1] if base else {})
    timetable = next(n for n in venue["nodes"] if n["id"] == "station_platform")["train_interval_min"]
    levers = {**DEFAULTS, "train_interval_min": timetable, **in_force}
    c = cost(in_force)
    if s["dangerous_zone_minutes"]:
        verdict = "UNSAFE"
    else:
        verdict = "MARGINAL" if s["peak_density"] >= ALERT_AT else "SAFE"
    return {
        "name": name,
        "plan": in_force,
        "actions": describe_levers(in_force) if plan is not None else ["No change"],
        "verdict": verdict,
        "dangerous_zone_minutes": s["dangerous_zone_minutes"],
        "alert_zone_minutes": int((zones["density"] >= ALERT_AT).sum()),
        "worst_zone": labels[s["peak_zone"]],
        "peak_density": s["peak_density"],
        "stadium_cleared_by_min": s["stadium_cleared_by_min"],
        "trains_per_hour": round(60 / levers["train_interval_min"], 1),
        "extra_buses": c["extra_buses_est"],
        "effort": c["effort"],
        "approve_by_minute": (approval_deadline(venue, plan, from_minute=minute, base_plans=base)
                              if plan is not None and verdict != "UNSAFE" else None),
    }


class ApproveRequest(BaseModel):
    plan: Dict[str, Any]
    minute: int


@app.post("/approve")
async def approve(req: ApproveRequest):
    """Add a plan to the approval log from req.minute, rebuild crowd_live.usda, return new scores."""
    s = _snapshot()
    try:
        apply_plan(s["venue"], req.plan)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: _approve_sync(req.plan, req.minute))


def _approve_sync(plan, minute):
    with _lock:
        before = [p for p in _state["plans"] if p[0] < minute]
        _state["plans"] = before + [(minute, plan)]
        log.info("Approved at minute %d: %s (plans in force: %d)", minute, plan, len(_state["plans"]))
        _rebuild_live()
        venue, plans = _state["venue"], list(_state["plans"])
    # What the command centre expects (belief), not the hidden truth
    plan_score, baseline_score = score(run_log(venue, plans), minute), score(run_log(venue, before), minute)
    _audit("approval", minute, f"Approved {_which_option(plan)}: " + "; ".join(describe_levers(plan)),
           plan=plan, expected_dangerous=plan_score["dangerous_zone_minutes"],
           baseline_dangerous=baseline_score["dangerous_zone_minutes"])
    log.info("Expected dangerous zone-minutes: %d → %d",
             baseline_score["dangerous_zone_minutes"], plan_score["dangerous_zone_minutes"])
    return {
        "minute": minute,
        "plan": plan,
        "score": plan_score,
        "baseline_score": baseline_score,
        "baseline_label": "keeping the current plan" if before else "doing nothing",
        "plans_in_force": [[m, p] for m, p in plans],
        "crowd_live_written": True,
        "crowd_live_path": str(CROWD_LIVE_FILE),
    }


def _which_option(plan):
    """Which option the operator approved: the AI's pick, another scored option, or their own plan."""
    card = _state.get("last_card")
    clean = {k: v for k, v in plan.items() if v != DEFAULTS.get(k)}
    for oid, o in (card or {}).get("options", {}).items():
        if o["levers"] == clean:
            name = oid.replace("_", " ")
            return f"the AI pick ({name})" if oid == card["recommended"] else f"option '{name}' (not the AI pick)"
    return "the operator's own plan"


class KeepRequest(BaseModel):
    minute: int


@app.post("/decision/keep")
def keep_current(req: KeepRequest):
    """The operator dismissed a re-plan card and kept the plan in force (logged for the audit trail)."""
    _audit("keep", req.minute, "Kept the current plan (re-plan card dismissed)")
    return {"status": "logged"}


@app.get("/audit")
def audit(limit: int = Query(50, ge=1, le=50)):
    return list(_audit_tail)[-limit:]


@app.post("/reset")
async def reset():
    """Back to minute 0 with nothing approved. A live run gets a fresh random world."""
    s = _snapshot()

    def _reset():
        with _lock:
            _state["last_card"] = None
            _state["generation"] += 1
        if s["world"] is not None:
            _load_scenario_sync("live")
        else:
            with _lock:
                _state["plans"] = []
                _rebuild_live()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _reset)
    _audit("reset", 0, "Reset to minute 0, nothing approved")
    return {"status": "reset", "crowd_live_path": str(CROWD_LIVE_FILE)}


# ------------------------------------------------------------------ web dashboard (static, no build step)
app.mount("/dashboard", StaticFiles(directory=ROOT / "web" / "dashboard", html=True), name="dashboard")


# ------------------------------------------------------------------ dev runner
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("odtcc.server:app", host="0.0.0.0", port=8000, reload=False)
