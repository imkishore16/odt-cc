# ODT-CC Brain Service API

FastAPI service in `odtcc/server.py`, port 8000. Interactive schema at `http://localhost:8000/docs`.
Both the Kit panel and the web dashboard use only these endpoints.

All numbers shown to the operator come from the command centre's **belief** (sensor estimates, reported
incidents). The hidden truth only drives the 3D twin and the sensors (see ARCHITECTURE.md §4).

## Status and state

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | – | `{"status": "ready"}` |
| GET | `/state` | `minute`, `client=kit\|dashboard` | Zones (estimate now, +15 min forecast, error band, confidence, alert band), alerts, sensors, incidents, reports, plans in force, latest decisions (`audit`), `card_id`; for `client=kit` also queued `commands`. Advances the world agent to `minute`. |
| GET | `/snapshot` | – | `{clock, state, card}` at the minute the driving client is at (no world tick). The dashboard polls this. |
| GET | `/card` | – | The latest recommendation card (404 if none) |
| GET | `/audit` | `limit` (≤50) | Latest decision-log entries (full log: `data/audit_log.jsonl`) |

## Scenarios and incidents

| Method | Path | Body | Effect |
|---|---|---|---|
| GET | `/scenarios` | – | Presets + `live`, current scenario, zone list, incident kinds, inflow limits |
| POST | `/scenario` | `{"id": "normal"}`; `{"id": "live", "seed": 7}`; `{"id": "custom", "incidents": [...]}`; `{"id": "planned", "planning": {...}}` | Switch scenario, clear approvals, rebuild the live crowd layer |
| POST | `/incident/inject` | `{"minute": 12, "kind": "rail_signal_failure"}` (`kind` optional; `<kind>_unreported` for an unreported one) | Live scenario only: the world agent strikes now |
| POST | `/reset` | – | Back to minute 0, nothing approved (live: a fresh random world) |

Custom incident: `{"type": "capacity" | "inflow" | "sensor_outage", "node": "<zone id>", "from": 10, "to": 25,
"factor": 0.0 (capacity) | "per_min": 400 (inflow)}`.

Planning inputs: `attendance` (40,000–80,000), `stagger_share` (0–0.5), `stagger_delay` (0–30),
`nearby_event_people` (0–20,000), `nearby_event_minute` (0–40), `train_interval` (2–8), `train_capacity`
(600–1,200), `close_vermont`, `close_exposition` (booleans).

## Decisions

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/recommend` | `{"minute": 3}` | Action card: `recommended`, `plan`, `actions`, `briefing`, `reason`, `why`, `trade_off`, `options` (each with `score_0_to_100`, `score_parts`, levers, peak, clear time, approval deadline), `do_nothing` + `baseline_label`, `expected`, `approve_by_minute`, `no_change`, `no_fully_safe_plan`, `source`, `card_id` (`stale: true` if a reset happened meanwhile) |
| GET | `/levers` | – | Lever definitions: label, owning agency, min, max, default |
| POST | `/simulate` | `{"plan": {...}, "minute": 0}` | Verdict SAFE / MARGINAL / UNSAFE, score, first danger, cost, approval deadline, baseline |
| POST | `/compare` | `{"plans": [{"name": "Plan A", "plan": {...}}], "minute": 0, "planning": {...}}` | Baseline + one row per plan: verdict, dangerous zone-minutes, zone-minutes above alert, worst bottleneck, clear time, trains/hour, extra buses, effort, approval deadline; planning notes |
| POST | `/approve` | `{"plan": {...}, "minute": 3}` | Adds a phase to the approval log, rewrites `usd/layers/crowd_live.usda`; returns expected score vs baseline |
| POST | `/decision/keep` | `{"minute": 12}` | Logs "kept the current plan" |

Plan levers: `stadium_release_per_min` (800–2,500), `train_interval_min` (2–4), `shuttle_outflow_per_min`
(150–450), `north_to_shuttle_share` (0.4–0.8), `east_to_south_share` (0–0.8).

## Driving Kit from the dashboard

| Method | Path | Body | Effect |
|---|---|---|---|
| POST | `/command` | `{"action": "play" \| "pause" \| "reset"}`; `{"action": "scenario", "id": ..., "planning"?: ..., "incidents"?: ...}`; `{"action": "approved", "data": <approve response>}` | Queued for the Kit panel and delivered in its next `/state` (Kit polls every second). 409 if Kit is not connected. Commands run strictly in order. |

## Static

| Path | Serves |
|---|---|
| `/dashboard/` | The web dashboard (`web/dashboard/`) |
