# ODT-CC User Guide

How to install, start and operate ODT-CC. For how it works inside, see [ARCHITECTURE.md](ARCHITECTURE.md).

Contents
1. [Requirements](#1-requirements)
2. [Install](#2-install)
3. [Start everything](#3-start-everything)
4. [The web dashboard](#4-the-web-dashboard)
5. [Planning mode: before the event](#5-planning-mode-before-the-event)
6. [Operations mode: event day](#6-operations-mode-event-day)
7. [Scenarios and incidents](#7-scenarios-and-incidents)
8. [The Kit panel and the 3D twin](#8-the-kit-panel-and-the-3d-twin)
9. [Fallbacks](#9-fallbacks)
10. [Command-line tools](#10-command-line-tools)
11. [Tests](#11-tests)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Requirements

| Need | Used for | Notes |
|---|---|---|
| Linux server with 2 NVIDIA GPUs (built on 2 × L40S, 46 GB) | GPU 0: Omniverse Kit; GPU 1: Nemotron NIM | One large GPU can work if the NIM profile fits beside Kit |
| Python 3.12 virtual environment | Brain service, simulator, forecaster, optimizer | `requirements.txt` |
| NVIDIA Omniverse Kit app template, built | 3D twin and the Kit panel | Expected at `~/Desktop/kit-app-template` (`scripts/launch_editor.sh`) |
| Docker + NGC API key | Nemotron 3.5 Lightning NIM container named `nemotron` on port 8001 | Without it, cards fall back to saved or template cards |
| A desktop session (`DISPLAY=:1`) | Kit window | |
| Chrome or Firefox | Web dashboard | |

## 2. Install

```bash
git clone <repo-url> && cd Nvidia
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt --extra-index-url=https://pypi.nvidia.com

# One-time: data, twins and the forecaster (all already in the repo; re-run only to regenerate)
python -m odtcc.scenarios          # baseline + demo-plan simulations → data/
python -m odtcc.build_twin         # OpenUSD twins → usd/
python -m odtcc.predictor train    # forecaster on GPU (~35 s) → models/
```

**Nemotron NIM** (one-time, needs an NGC key): pull `nvcr.io/nim/nvidia/nemotron-3.5-lightning-30b-a3b`,
run it as a container named `nemotron` on GPU 1, port 8001, with tool calling enabled. On a 46 GB L40S use the
mixed FP8 / 4-bit profile (BF16 needs about 63 GB). Check with `python scripts/nim_smoke_test.py`.

## 3. Start everything

```bash
scripts/demo_up.sh       # NIM (waits until ready) → brain service :8000 → Kit editor with the panel
scripts/warm_caches.sh   # optional: saves a Nemotron card per scenario for the offline fallback
```

Then open **http://localhost:8000/dashboard/** and maximize the Kit window beside it.

`demo_up.sh` leaves running services alone. To load new code, restart the brain service:
`kill $(ss -ltnpH 'sport = :8000' | grep -oP 'pid=\K\d+'); nohup .venv/bin/uvicorn odtcc.server:app --port 8000 --host 0.0.0.0 > ~/server.log 2>&1 &`

## 4. The web dashboard

| Area | What it shows |
|---|---|
| Header | Minute after the final whistle, mode badge, **Play / Pause / Reset** |
| Bar | **Scenario** picker, **View as** (role-based view), sensor strip (Bluetooth online, tickets scanned, last train) |
| Banners | Active incident (red), alert (red/amber), "Plan in force" (green), approval countdown |
| Operations column | Recommendation card, 3 scored options, zone table with 15-minute forecast and confidence, incident feed with **Inject** |
| Planning column | **Your plan** sliders and Simulate, **Compare plans** with planned-event inputs, **Decision log** |

**Mode badge**
* **Following Kit (3D twin):** Kit is running. The dashboard's Play, Pause, Reset, scenario switches and
  approvals drive Kit's timeline; the 3D view and the dashboard always show the same run.
* **Standalone clock:** Kit is not connected (or `?standalone=1`). The dashboard runs its own clock.
  Use one clock at a time.

## 5. Planning mode: before the event

Work at minute 0 (Reset first).

1. **Your plan:** set the five levers, click **Simulate**. Verdict: **SAFE** (no dangerous minutes, peak under
   4.5), **MARGINAL** (no danger, peak 4.5–5) or **UNSAFE** (shows the first zone and minute in danger).
   Unsafe plans cannot be approved.
2. **Save as Plan A / Plan B.** Click **Recommend now**, then **Add AI plan** to include Nemotron's pick.
3. **Planned event** (applies to every compared plan):

   | Input | Meaning |
   |---|---|
   | Tickets sold | Crowd size, 40,000–80,000 |
   | Held for a 2nd wave, leaves after | Staggered session end: that share stays seated until the second wave |
   | Nearby event crowd, lets out at | An overlapping session; its people head for the station walkway over 20 minutes |
   | Timetable: train every, people per train | The planned Metro service (a plan's own train lever still overrides it) |
   | Road closures | Vermont Ave (parking exits at 40%), Exposition Blvd (shuttles load at 50%) |

4. **Compare:** one table (verdict, dangerous zone-minutes, minutes above alert, worst bottleneck, stadium clear
   time, trains per hour, extra buses, effort, approval deadline) plus peak-density bars against the danger line.
5. **Run this event live** turns the planned event into the operations scenario ("Planned event").

## 6. Operations mode: event day

1. Pick a scenario and press **Play**. One simulated minute plays every quarter second.
2. The run **pauses automatically** when the forecast raises a new alert, or when a new incident is reported.
3. A **recommendation card** appears in a few seconds: Nemotron's briefing, why-bullets, a before/after table
   ("doing nothing" or "keeping the current plan" vs "if approved now") and the approval deadline.
4. **Options** are scored 0–100 (safety 45, clear speed 20, ease 20, time to act 15). Each lists its actions
   tagged with the owning agency, e.g. `[LA Metro] Run a train every 2 min`.
5. Decide:
   * **Approve AI plan**, or **Approve this option** on any option, or **Approve my plan** from Your plan
   * **Keep current plan** (re-plans only) to dismiss the card
   * A card may say **No change needed** or **ESCALATE** (no lever combination prevents the danger)
6. The run resumes; the 3D twin follows the approved plan. Every decision appears in the **Decision log**
   (full trail in `data/audit_log.jsonl`).
7. **View as** an agency to see only that agency's tasks.

## 7. Scenarios and incidents

| Scenario | What happens | Typical first pause |
|---|---|---|
| Normal egress | 70,000 people leave at the final whistle | minute 3 |
| Live: random incidents | Normal, plus the world agent causes realistic incidents at random (up to 2, from minute 5) | minute 3, then per incident |
| Expo Line delay | No trains minutes 10–25 (known disruption) | minute 2 |
| BMO Stadium event | 400 extra people/min onto the walkway, minutes 15–35 | minute 2 |
| Walkway sensor outage | Walkway Bluetooth counters dark minutes 0–60 | minute 4 (platform); walkway still flagged ~6 |
| Custom incident | Built in the Kit panel's incident builder | – |
| Planned event | From "Run this event live" | – |

**Inject incident now** (live scenario): choose a type or "random". *Rail signal failure* or *nearby event*
give a visible re-plan; *nearby event unreported* is not reported, and the system detects it from sensor data
within a few minutes ("ODT-CC anomaly detection (sensors)").

## 8. The Kit panel and the 3D twin

The Kit panel (docked right of the viewport) has the same functions as the dashboard, plus an **incident
builder** (type, zone, start, duration, size → Apply). In the viewport:

| Visual | Meaning |
|---|---|
| Pad and pillar colour | Density: green → yellow → orange → red (5+ people/m²) |
| Pillar height | 10 m per person/m² |
| Blue disc on the bowl | People still inside the stadium |
| Block at the platform | A train departing that minute |
| Pulsing sphere | Incident zone (red: capacity or crowd, amber: sensors) |

The viewport shows the true crowd; the dashboard and panel show what the sensors estimate.

## 9. Fallbacks

| If | Then |
|---|---|
| Nemotron NIM is down or slow | The server replays the saved card for the same minute and incident (`cached:nemotron-local`), otherwise a template card from the optimizer's numbers. Run `scripts/warm_caches.sh` beforehand. |
| Kit is not running | The dashboard switches to its standalone clock; everything except the 3D view works |
| The brain service restarts | Kit and the dashboard reconnect automatically (retry every 2 s) |

## 10. Command-line tools

```bash
python -m odtcc.optimizer --minute 3 --scenario expo_line_delay   # best plans → data/optimizer_result_<id>.json
python -m odtcc.agent --minute 3 --scenario normal                # action card in the terminal
python -m odtcc.agent --mode template                             # also: cached, hosted
python -m odtcc.predictor demo                                    # alert lead times per scenario
python scripts/nim_smoke_test.py                                  # NIM chat, tool call, briefing
```

## 11. Tests

`tests/test_dashboard_e2e.py` drives the dashboard in headless Chrome with Playwright (11 checks: auto-pause,
card, approve, decision log, inject, re-plan, non-AI approval, Your plan verdict, comparison table, planned-event
notes). Run it in a separate virtual environment:

```bash
python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright
/tmp/pw/bin/python tests/test_dashboard_e2e.py standalone   # or: kit (needs Kit running)
```

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| Badge says "Standalone" while Kit is open | Kit isn't reaching the server: check `~/editor.log` for `odtcc` errors, restart Kit |
| "server offline" | Start the brain service; see `~/server.log` |
| Kit asks to "fetch changes" | Normally suppressed by the panel (auto-reload); click Fetch |
| Kit opens without the twin | Click **Open live twin** in the panel (the server must be running so `usd/twin_live.usda` exists) |
| Card source `template` | NIM unreachable: `curl localhost:8001/v1/health/ready` |
| Errors in Kit | `grep -nE "odtcc|Traceback" ~/editor.log` |
