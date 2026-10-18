# ODT-CC Architecture

ODT-CC (Olympic Digital Twin Command Center) is a digital twin of the LA Memorial Coliseum / Exposition Park
venue zone for the LA28 Games. It forecasts crowd danger 15 minutes ahead from sensor data, searches for
operator plans that prevent it, explains the best options with a self-hosted Nemotron model, and applies the
plan the operator approves to a live OpenUSD twin in NVIDIA Omniverse.

This document explains how the pieces fit together. For running it, see [USER_GUIDE.md](USER_GUIDE.md); for
the HTTP interface, [API.md](API.md); for numbers and assumptions, [DATA_AND_RESULTS.md](DATA_AND_RESULTS.md).

Contents
1. [System overview](#1-system-overview)
2. [Runtime and deployment](#2-runtime-and-deployment)
3. [The decision loop](#3-the-decision-loop)
4. [Truth, sensors and belief](#4-truth-sensors-and-belief)
5. [The venue model and simulator](#5-the-venue-model-and-simulator)
6. [Forecaster](#6-forecaster)
7. [Optimizer and option scores](#7-optimizer-and-option-scores)
8. [Nemotron agent and guardrails](#8-nemotron-agent-and-guardrails)
9. [Incidents: world agent and detection](#9-incidents-world-agent-and-detection)
10. [Planning mode and operations mode](#10-planning-mode-and-operations-mode)
11. [The OpenUSD twin](#11-the-openusd-twin)
12. [Control surfaces: Kit panel and web dashboard](#12-control-surfaces-kit-panel-and-web-dashboard)
13. [Product modules mapped to code](#13-product-modules-mapped-to-code)
14. [Target production architecture](#14-target-production-architecture)

---

## 1. System overview

```mermaid
flowchart TB
    subgraph UI["Control surfaces (same HTTP API)"]
        direction LR
        DASH["Web dashboard<br/>web/dashboard"]
        PANEL["Kit panel<br/>odtcc.command_center"]
    end

    SRV["Brain service · FastAPI :8000<br/>server.py: state · approvals · audit"]
    DASH --> SRV
    PANEL --> SRV

    WLD["World agent<br/>world.py"] --> SRV

    subgraph PIPE["Prediction pipeline"]
        direction LR
        SIM["Simulator"] --> SEN["Synthetic sensors"] --> FC["Forecaster<br/>XGBoost +15 min"]
    end
    SRV --> PIPE

    SRV --> USDW["Twin writer<br/>build_twin.py"]
    SRV --> AG["Agent<br/>agent.py"]
    AG --> OPT["Optimizer<br/>2,250 plans"]

    USDW -- "crowd_live.usda<br/>(auto-reload)" --> VIEW
    AG -- "tool calling" --> NIM

    subgraph G0["GPU 0 · NVIDIA Omniverse Kit"]
        VIEW["RTX viewport<br/>usd/twin_live.usda"]
    end
    subgraph G1["GPU 1 · Docker"]
        NIM["Nemotron 3.5 Lightning NIM<br/>:8001"]
    end
```

**One brain, two control surfaces, one 3D view.** All computation runs in the Python brain service. The Kit
extension and the web dashboard are thin clients of the same HTTP API, so they always show the same state.
The brain writes the live crowd layer of the OpenUSD twin, and Kit renders it with RTX.

| Component | Code | Technology |
|---|---|---|
| Venue model | `config/venue.json` | 11 nodes: bowl, 4 gates, 2 plazas, station walkway, platform, shuttle stop, parking |
| Crowd simulator | `odtcc/generate_data.py`, `odtcc/scenarios.py` | Minute-by-minute flow network with density cap and back-pressure |
| Synthetic sensors | `odtcc/sensors.py` | Ticket scans, Bluetooth zone counts, gate counters, train departures |
| Forecaster | `odtcc/predictor.py` | XGBoost (trained on CUDA), 15-minute horizon |
| Optimizer | `odtcc/optimizer.py` | Exhaustive sweep of 2,250 lever combinations, parallel on 32 CPU cores |
| Agent | `odtcc/agent.py` | Nemotron 3.5 Lightning 30B-A3B NIM, tool calling, number guardrails |
| World agent | `odtcc/world.py` | Random realistic incidents during a live run |
| Brain service | `odtcc/server.py` | FastAPI on port 8000 |
| Digital twin | `odtcc/build_twin.py`, `usd/` | Layered OpenUSD, rendered in Omniverse Kit 110 (RTX Real-Time 2.0) |
| Kit panel | `kit/exts/odtcc.command_center/` | omni.ui extension |
| Web dashboard | `web/dashboard/` | Static HTML/JS served by the brain service |

---

## 2. Runtime and deployment

```mermaid
flowchart TB
    subgraph BOX["Launchpad GPU server · 2 × NVIDIA L40S"]
        subgraph G0["GPU 0"]
            KITP["Kit Base Editor<br/>+ odtcc.command_center"]
        end
        subgraph G1["GPU 1"]
            NIMC["Docker: nemotron NIM<br/>port 8001"]
        end
        subgraph CPU["CPU · .venv"]
            API["uvicorn odtcc.server:app<br/>port 8000"]
            POOL["Optimizer process pool<br/>32 workers"]
        end
        FILES[("usd/ · data/ · models/<br/>config/")]
    end
    BROWSER["Browser<br/>localhost:8000/dashboard/"]

    BROWSER --> API
    KITP <--> API
    API --> NIMC
    API --> POOL
    API <--> FILES
    KITP --> FILES
```

| Process | Start | Port | Log |
|---|---|---|---|
| Nemotron NIM | `docker start nemotron` (or `scripts/demo_up.sh`) | 8001 | `docker logs nemotron` |
| Brain service | `uvicorn odtcc.server:app --port 8000` | 8000 | `~/server.log` |
| Kit editor + panel | `scripts/launch_editor.sh` | – | `~/editor.log` |
| Web dashboard | served by the brain service | 8000 `/dashboard/` | browser console |

`scripts/demo_up.sh` starts all three in order with health checks. Kit is pinned to GPU 0 so the NIM has
GPU 1 to itself.

---

## 3. The decision loop

Every minute of a run follows the same loop: sense, forecast, alert, plan, explain, approve, apply.

```mermaid
flowchart LR
    A["Sensors<br/>estimated density"] --> B["Forecast<br/>+15 min per zone"]
    B --> C{"Alert?<br/>≥ 4.5 p/m²"}
    C -- no --> A
    C -- "yes, or new<br/>incident report" --> D["Pause and<br/>optimize"]
    D --> E["3 safe options<br/>scored 0-100"]
    E --> F["Nemotron<br/>picks and explains"]
    F --> G["Operator<br/>approves or keeps plan"]
    G --> H["Approval log<br/>+1 plan phase"]
    H --> I["Re-simulate,<br/>rewrite crowd layer"]
    I --> A
```

The sequence below is a live run with an unscripted incident, as it happens in the demo.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant D as Dashboard / Kit panel
    participant S as Brain service
    participant W as World agent
    participant N as Nemotron NIM
    participant K as Kit twin

    Op->>D: Play
    loop every simulated minute
        D->>S: GET /state?minute=t
        S-->>D: zones, 15-min forecast, alerts, reports
    end
    Note over D: minute 3: walkway forecast 4.6 p/m²
    D->>D: auto-pause
    D->>S: POST /recommend
    S->>S: optimize 2,250 plans (~1 s)
    S->>N: tools: forecast, options, what-if
    N-->>S: pick + briefing (JSON)
    S-->>D: action card, 3 scored options
    Op->>D: Approve
    D->>S: POST /approve {plan, minute}
    S->>K: rewrite crowd_live.usda (auto-reload)
    D->>D: resume
    W->>S: incident at minute 12 (rail signal failure)
    S-->>D: report "LA Metro alert"
    D->>D: auto-pause
    D->>S: POST /recommend (on top of the plan in force)
    S-->>D: re-plan card: "keeping the current plan → N dangerous minutes"
    Op->>D: Approve an option
    D->>S: POST /approve
    S->>K: rewrite crowd_live.usda
```

**Approval minute.** A plan takes effect from the minute it is approved, not from minute 0. The simulator keeps
an approval log `[(minute, plan), ...]`; each approval adds a phase. Incidents only act from their own start
minute, so re-simulating with a longer log reproduces every earlier minute exactly (tested: the past is
bit-identical).

---

## 4. Truth, sensors and belief

A real command centre never sees the true crowd. ODT-CC keeps three views apart.

```mermaid
flowchart LR
    subgraph TRUTH["Truth (hidden)"]
        T1["Simulator with real incidents<br/>(real durations)"]
    end
    subgraph SENS["What the feeds report"]
        S1["Ticket scans<br/>±0.2%"]
        S2["Bluetooth counts<br/>~30% of phones, ±8%,<br/>2% missed minutes, outages"]
        S3["Gate exit counters<br/>±1%"]
        S4["Train departures<br/>GTFS-realtime style"]
    end
    subgraph BELIEF["Command centre"]
        B1["Estimated density<br/>3-minute average"]
        B2["Reported incidents<br/>typical durations"]
        B3["Forecaster, optimizer,<br/>agent, all numbers shown"]
    end
    T1 --> S1 & S2 & S3 & S4
    S1 & S2 & S3 & S4 --> B1 --> B3
    B2 --> B3
    T1 -. "3D twin shows the truth" .-> TW["Kit viewport"]
```

* **Truth** drives the 3D twin and the sensors.
* **Sensor estimates** drive the zone table and the forecaster (which was trained on estimates, with random
  outages, to predict the *true* density).
* **Belief**, the reported incidents with the duration the report gives, drives the optimizer and the agent.
  If an incident lasts longer than reported, follow-up reports ("still not resolved") extend the belief.

---

## 5. The venue model and simulator

```mermaid
flowchart LR
    ST(["Coliseum bowl<br/>70,000 people"])
    GN["North gates"]
    GE["East gates"]
    GS["South gates"]
    GW["West gates"]
    PN["North plaza /<br/>Rose Garden"]
    PS["South plaza"]
    WK["Walkway to<br/>Expo Park/USC"]
    PL["Expo Park/USC<br/>platform"]
    SH["Shuttle bus<br/>loading"]
    PK["Parking lots /<br/>Vermont Ave"]
    OUT((("leaves<br/>the zone")))

    ST -- 35% --> GN
    ST -- 25% --> GE
    ST -- 20% --> GS
    ST -- 20% --> GW
    GN --> PN
    GE -- "100% by default" --> PN
    GE -. "lever: 0-80%<br/>sent south" .-> PS
    GS --> PS
    GW --> PS
    PN -- "60%<br/>(lever)" --> WK
    PN -- "40%<br/>(lever)" --> SH
    PS -- 20% --> WK
    PS -- 80% --> PK
    WK --> PL
    PL -- "1,000 per train<br/>every 4 min (lever)" --> OUT
    SH -- "150/min (lever)" --> OUT
    PK -- 300/min --> OUT
```

Each minute, each node passes up to its capacity downstream, split by route shares. People can only move into
space that exists: a zone at the physical maximum of 6 people/m² blocks its senders, so queues propagate
backwards like real crowds. Density bands follow Fruin levels of service; **5 people/m² is dangerous** and
the forecaster **alerts at 4.5**. One simulation of 120 minutes takes about 7 ms.

**Operator levers** (with realistic limits) and the agency that carries each out:

| Lever | Default | Range | Owner |
|---|---|---|---|
| Release from the bowl | 2,500/min | 800–2,500 | LA28 Venue Ops |
| Train every | 4 min | 2–4 min | LA Metro |
| Shuttle capacity | 150/min | 150–450 | LA28 Transport / LADOT |
| North plaza to shuttles | 40% | 40–80% | Venue stewards / LAPD |
| East gates to south | 0% | 0–80% | Venue stewards / LAPD |

**Incidents** are part of the venue: `capacity` (a zone's throughput × factor for a window), `inflow` (extra
people per minute, e.g. another event letting out), `sensor_outage` (a zone's counters go dark).
**Planning inputs** change the event itself: tickets sold, a staggered second wave, an overlapping nearby
event, the train timetable, and road closures (expressed through the capacity they remove).

---

## 6. Forecaster

```mermaid
flowchart LR
    F1["Sensor estimates<br/>now, -1, -5 min<br/>(NaN when offline)"] --> X
    F2["People left in the bowl"] --> X
    F3["Levers in force"] --> X
    F4["Zone, minute, 5-min trend"] --> X
    X["XGBoost regressor<br/>600 trees, depth 8"] --> Y["True density<br/>15 min ahead"]
    Y --> Z{"≥ 4.5 caution<br/>≥ 5 danger"}
```

* Trained on CUDA from 600 simulated egress runs: 45–80k attendance, random plans approved mid-run, 30% with a
  random incident, 30% with a random sensor outage. Inputs are sensor estimates; the target is the true
  density. About 33 s.
* Held out: **97% of dangers caught**, 83% of alerts real, average error 0.29 people/m² in busy zones.
* Each forecast carries a **confidence** (high / medium / low from sensor status) and an error band from the
  held-out error.

---

## 7. Optimizer and option scores

```mermaid
flowchart LR
    A["Grid: 6 × 3 × 5 × 5 × 5<br/>= 2,250 lever combinations"] --> B["Simulate each from<br/>the decision minute,<br/>on top of plans in force"]
    B --> C["Score: dangerous zone-minutes<br/>from now on, peak, clear time"]
    C --> D{"Any with 0<br/>dangerous minutes?"}
    D -- yes --> E["least effort<br/>most margin<br/>fastest clear"]
    D -- no --> F["Same three among the least<br/>dangerous + ESCALATE flag"]
    E & F --> G["Approval deadline<br/>+ 5 noise seeds robustness<br/>+ 0-100 score"]
```

* The sweep runs in about 1.1 s on 32 cores.
* **Option score (0–100), computed by code:** safety 45 (headroom under the danger line, 0 if any dangerous
  minute), clear speed 20, ease 20 (1 − effort/5), time to act 15 (minutes left to approve).
* **Approval deadline:** the last minute at which approving the plan still prevents all danger.

---

## 8. Nemotron agent and guardrails

```mermaid
flowchart TB
    START["Alert or incident"] --> LLM["Nemotron 3.5 Lightning<br/>(local NIM, tool calling)"]
    LLM -- "get_forecast" --> T1["Forecast tool"]
    LLM -- "get_plan_options" --> T2["Optimizer options<br/>+ incident + baseline"]
    LLM -- "simulate_plan" --> T3["What-if simulation<br/>(levers within limits)"]
    T1 & T2 & T3 --> LLM
    LLM --> J["JSON: pick, briefing, reason"]
    J --> V{"Guardrails"}
    V -- "pick not an option,<br/>bad JSON" --> R["Correction round (≤2)"]
    V -- "a number not in<br/>the tool results" --> R
    R --> LLM
    V -- ok --> CARD["Action card<br/>numbers and comparisons<br/>written by code"]
    V -- "still failing,<br/>LLM down" --> FB["Saved card for the same<br/>minute and incident,<br/>else template card"]
    FB --> CARD
    CARD --> HUMAN["Operator approves<br/>(the agent never acts)"]
```

**Division of labour:** Nemotron chooses and explains; code writes every number, comparison, why-bullet and
trade-off. Early versions that let the model compare numbers got them wrong ("4.7 is above 5"), so the model
now only quotes numbers that appear in its tool results, and a checker rejects anything else.

---

## 9. Incidents: world agent and detection

```mermaid
flowchart LR
    W["World agent<br/>random incidents from a catalog<br/>(odds, size, real duration)"] --> R{"Reported?"}
    R -- "yes (LA Metro alert,<br/>LAFD dispatch, ...)" --> REP["Report to the<br/>command centre<br/>typical duration"]
    R -- "no (about half of<br/>nearby-event crowds)" --> HID["Only in the truth"]
    HID --> SENS["Sensors see a<br/>build-up"]
    SENS --> DET{"Estimate above expected<br/>by max(0.5, 30%)<br/>for 3 minutes?"}
    DET -- yes --> DETR["Detection report<br/>+ assumed inflow"]
    REP & DETR --> PAUSE["Pause → re-plan<br/>on top of current plan"]
    REP --> UPD["Follow-ups:<br/>still ongoing / cleared"]
```

The catalog: E Line signal failure, reduced train service, medical emergency on the walkway, a nearby event
(BMO Stadium, Natural History Museum, California Science Center) letting out, shuttle breakdowns, Bluetooth
sensor failure. Up to 2 incidents between minutes 5 and 45, at least 8 minutes apart; the operator can also
inject one on demand. A twin cannot predict that an incident *starts*; it detects it and simulates its
consequences.

---

## 10. Planning mode and operations mode

```mermaid
flowchart LR
    subgraph PLAN["Planning mode · before the event"]
        P1["Planned event inputs<br/>tickets, 2nd wave, nearby event,<br/>timetable, road closures"]
        P2["Your plan<br/>Simulate → SAFE / MARGINAL / UNSAFE"]
        P3["Compare Plan A, Plan B, AI<br/>table + peak bars"]
        P1 --> P3
        P2 --> P3
    end
    subgraph OPS["Operations mode · event day"]
        O1["Live run: sensors, forecast,<br/>alerts, incidents"]
        O2["Re-plan cards,<br/>approvals"]
        O3["Decision log<br/>(audit trail)"]
        O1 --> O2 --> O3
    end
    P3 -- "Run this event live" --> O1
```

---

## 11. The OpenUSD twin

```mermaid
flowchart TB
    TL["usd/twin_live.usda<br/>(opened in Kit)"]
    CL["layers/crowd_live.usda<br/>stronger: time-sampled overrides<br/>pad colour, pillar height, density,<br/>bowl fill, trains, incident beacons"]
    VL["layers/venue_layout.usda<br/>weaker: static venue<br/>ground, bowl, zones, paths, lights, camera"]
    TL -- sublayer --> CL
    TL -- sublayer --> VL
    SRV["Brain service"] -- "rewrites on approve,<br/>reset, scenario, incident" --> CL
```

Kit's sublayer auto-reload is switched on by the extension, so a rewritten crowd layer appears in the viewport
without reopening the stage. Visual encoding: pad and pillar colour green → yellow → orange → red by density,
pillar height 10 m per person/m², a blue disc on the bowl for people still inside, a train block at the
platform on each departure, a pulsing red (or amber, for sensors) beacon over an incident zone.

---

## 12. Control surfaces: Kit panel and web dashboard

```mermaid
flowchart LR
    subgraph KM["Following Kit (Kit running)"]
        D1["Dashboard"] -- "POST /command<br/>play, pause, reset,<br/>scenario, approved" --> Q[("Command queue")]
        Q -- "delivered in Kit's<br/>/state, in order" --> K1["Kit panel<br/>owns the timeline"]
        K1 -- "GET /state each minute<br/>+ 1 s heartbeat" --> SV["Brain service"]
        D1 -- "GET /snapshot<br/>every 0.5 s" --> SV
    end
    subgraph SA["Standalone (no Kit)"]
        D2["Dashboard<br/>owns the clock"] -- "GET /state?client=dashboard" --> SV2["Brain service"]
    end
```

Both surfaces share one recommendation card (`card_id`) and one decision log. Safety rules learned in testing:
commands run strictly in order; a card computed before a reset is marked stale and never shown; after a reset
the panel ignores timeline minutes until Kit has applied minute 0; a new incident always stops the clock.

---

## 13. Product modules mapped to code

| Module | What it does | Where |
|---|---|---|
| 1. Scenario planner | Planned-event inputs, Simulate, compare plans, run the planned event live | `/compare`, `/simulate`, `scenarios.planning_venue`, dashboard Planning column |
| 2. Live operations view | 3D twin, sensor strip, zone table, alerts, incident feed, role-based views | Kit viewport, `/state`, dashboard Operations column |
| 3. Short-horizon forecasting | 15-min forecast from sensors with confidence | `predictor.py`, `sensors.py` |
| 4. Incident response simulator | Incident builder, world agent, detection, re-plan on top of the plan in force | `world.py`, `/incident/inject`, `/scenario custom`, `_detect` |
| 5. Recommendations with human approval | Nemotron card, scored options, approve / keep current, audit trail | `agent.py`, `/recommend`, `/approve`, `data/audit_log.jsonl` |

---

## 14. Target production architecture

```mermaid
flowchart LR
    subgraph FEEDS["Data connectors"]
        F1["Ticketing scans<br/>(LA28)"]
        F2["GTFS-realtime<br/>(LA Metro)"]
        F3["Traffic: ATSAC, PeMS<br/>(LADOT, Caltrans)"]
        F4["Camera counts via<br/>Metropolis (aggregate only)"]
    end
    subgraph CORE["Prediction services · NVIDIA GPUs"]
        C1["Crowd + transit simulation"]
        C2["SUMO road traffic"]
        C3["Forecasters"]
        C4["Optimizer"]
        C5["Nemotron agents"]
    end
    subgraph VIS["Omniverse (cloud)"]
        V1["OpenUSD twin<br/>GIS / LiDAR / BIM base"]
        V2["RTX streaming to browser"]
    end
    subgraph UI["Command dashboard (web)"]
        U1["Planner · live ops ·<br/>approvals · audit · role views"]
    end
    FEEDS --> CORE --> VIS
    CORE <--> UI
    V2 --> UI
```

**What runs today vs. the production path:**

| Layer | Running today | Production path |
|---|---|---|
| Data | Synthetic feeds with realistic noise and outages | Ticketing, GTFS-realtime, traffic, aggregate camera counts |
| Simulation | Pedestrian + transit flow network | Plus SUMO road traffic on an OpenStreetMap network |
| 3D | Omniverse Kit on the GPU server | Omniverse in the cloud, streamed to the browser |
| UI | Kit panel + web dashboard (same API) | Web dashboard with role-based access control |

The streaming Kit apps (`my_usd_viewer_streaming`, `my_editor_streaming`) are already built on the server; see
[docs/overview.md](overview.md) for the earlier browser-streaming trial.
