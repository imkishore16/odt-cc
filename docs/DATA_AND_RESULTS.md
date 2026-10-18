# ODT-CC Data, Assumptions and Results

What is real, what is assumed, and what the system measurably does. All data is synthetic; this page says how
it is shaped and where the real data would come from.

## 1. Assumptions register

| Input | Value used | Status | Real source in production (owner) |
|---|---|---|---|
| Crowd leaving | 70,000 at the final whistle | Assumed (the Coliseum seats about 77,000) | Ticket scan-ins per session (LA28 ticketing) |
| Exit timing | everyone starts leaving at minute 0; bowl exits up to 2,500/min | **Assumed**; real crowds trickle out early and linger | Gate exit counters, past-event egress curves (LA28 venue ops) |
| Gate split | north 35%, east 25%, south 20%, west 20% | Assumed | Seat map to gate assignment (LA28 venue ops) |
| Gate, plaza, walkway capacities | 650–750 / 1,400–1,500 / 600 people/min | Assumed | Venue layout and screening throughput (LA28 venue ops) |
| Zone areas and positions | 800 m² gates … 10,000 m² parking; schematic layout | Schematic | Venue CAD/BIM, temporary fencing plans |
| Mode split | 60% of the north plaza to the station walkway, 40% to shuttles | Assumed | Metro tap data, LA28 mode-split plan |
| Trains | every 4 min, 1,000 people per train | **Assumed** | Metro's public GTFS feed (headways), published car capacity |
| Shuttles | 150 people/min | Assumed | Shuttle fleet allocation (LA28 Transport, contractors) |
| Danger and alert lines | 5 and 4.5 people/m² | Fruin crowd levels of service | Standard practice |
| Bluetooth sensors | 30% of phones detected, ±8% noise, 2% missed minutes | Assumed sensor model | Calibrated against gate counts on the day |
| Incident odds and sizes | world-agent catalog (`odtcc/world.py`) | **Assumed** | Historical incident logs from past Coliseum and SoFi events (LAPD, LAFD) |
| Forecaster training data | 600 simulated runs | Synthetic | Historical event data |

Everything is configurable in `config/venue.json`, `config/scenarios.json` and the module constants.

**Production data sources** (most are already held by the organizing committee and the city):

| Feed | Owner | Access |
|---|---|---|
| Ticket scans, venue layout, staffing, screening lanes | LA28 | Internal |
| GTFS / GTFS-realtime train and bus positions | LA Metro | Public |
| Station tap counts | LA Metro | Data-sharing agreement |
| Traffic signals and sensors (ATSAC, PeMS) | LADOT, Caltrans | Data-sharing agreement |
| Camera crowd counts (NVIDIA Metropolis, aggregate only, no identities) | Venue security | Privacy review |
| Historical incident logs | LAPD, LAFD | Data-sharing agreement |

## 2. Results

### Scenarios (do nothing vs the recommended plan, from the minute the run pauses)

| Scenario | Pause | Do nothing | Recommended plan | Approve by | Nemotron card |
|---|---|---|---|---|---|
| Normal egress | min 3 | **99** dangerous zone-minutes, peak 6.44, clears min 28 | **0**, peak 0.98, clears min 81 | min 14 | 1.6 s |
| Expo Line delay | min 2 | **129**, peak 6.44 | **0**, peak 2.40, shuttle-heavy | min 6 | 1.9–2.3 s |
| BMO Stadium event | min 2 | **112**, peak 6.44 | **0**, peak 1.82 | min 10 | 2.2 s |
| Walkway sensor outage | min 4 | **99** | **0**, peak 1.09 | min 14 | 1.5–2.0 s |

**The trade-off is real and shown on the card:** the safe plan holds people in the bowl, so the stadium
clears at about minute 81 instead of 28.

### Planning mode example
70,000 tickets, 30% held for a second wave for 15 minutes, a 12,000-person event nearby letting out at
minute 15, trains every 5 minutes with 900 per train, Vermont Ave closed:

| Plan | Verdict | Dangerous zone-minutes | Peak | Clears | Extra buses |
|---|---|---|---|---|---|
| Do nothing | UNSAFE | 155 | 6.43 | min 28 | 0 |
| Plan A: "double the trains" | UNSAFE | 36 | 6.00 | min 28 | 0 |
| AI plan (most margin) | **SAFE** | 0 | 4.30 | min 87 | 56 |

### Forecaster (15 minutes ahead, from sensor estimates)

| Metric | Value |
|---|---|
| Dangers caught (recall) | 97% |
| Alerts that were real (precision) | 83% |
| Average error | 0.08 people/m² overall, 0.29 in busy zones |
| Training | 600 runs, 504,000 rows, on CUDA, ~33 s |

| Zone (normal egress, do nothing) | First alert | Danger starts | Warning |
|---|---|---|---|
| Walkway to Expo Park/USC | min 3 | min 18 | 15 min |
| Expo Park/USC platform | min 3 | min 19 | 16 min |
| North gates | min 7 | min 26 | 19 min |
| Shuttle bus loading | min 20 | min 37 | 17 min |

With the walkway sensors dead (sensor-outage scenario), the walkway is still flagged at minute 6 from the zones
around it (12 minutes before danger).

### Optimizer
* 2,250 lever combinations simulated per decision in about 1.2 s (32 CPU cores); 1,028 are safe at minute 3.
* Each option gets an approval deadline (latest safe approval minute) and a robustness check over 5 noise seeds.

### Sensors and detection
* Estimated density error: 0.07 people/m² on average, about 6% in busy zones.
* Unreported crowd surge (750 people/min onto the walkway, injected at minute 10): detected 4–6 minutes later
  from sensor data alone.
* No false detections in tests with approvals at minutes 3, 8, 13 and 20 (plus a second plan change 10 minutes
  later) and across random worlds.

### Agent
* Self-hosted Nemotron 3.5 Lightning 30B-A3B NIM on one L40S: 20/20 valid cards in the acceptance run, median
  2.0 s; typical live cards 1.5–4 s.
* Guardrails: the pick must be one of the optimizer's options; every number in the briefing must appear in the
  tool results; up to 2 correction rounds; otherwise a saved or template card. The agent never approves.

### Simulator
* 120 simulated minutes in about 7 ms. Re-simulating with an extra incident or approval reproduces every earlier
  minute exactly (tested).

### Dashboard end-to-end test
`tests/test_dashboard_e2e.py`, 11 checks per run in headless Chrome: standalone passes 11/11; driving Kit, the
latest runs pass 11/11 (occasional single-check timing failures were traced and fixed; see PLAN.md W1).

## 3. Limitations (say these out loud)

* The venue is schematic and all capacities are assumed; the forecaster is trained on the simulator, not on real
  crowds.
* Exit timing is simplified (everyone starts at the whistle).
* Road traffic is not simulated; road closures act only through the capacity they remove.
* The optimizer's what-if runs start from the simulated state; production would start them from the estimated
  state (data assimilation).
* The 3D twin shows the simulated truth; the panel and dashboard show sensor estimates.
* Egress only; ingress (arrivals, security screening) is future work.
