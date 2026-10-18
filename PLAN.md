# ODT-CC — Implementation Plan and Status

*Updated 18 Oct 2026 · Phases 0–4 done · **final sprint: 6 h of code, then 1 h for the pitch deck** (section 0) · team of 2 · demo runs in Kit Base Editor on the Launchpad desktop*

**Legend:** `[x]` done · `[ ]` to do · `(stretch)` only once the core loop works.
**Owners:** **M** = Mohamed (brain: sim, predictor, optimizer, agent, server) · **T** = teammate (face: twin, Kit extension, pitch).

---

## 0. Final sprint (18 Oct): 6 hours of code, then 1 hour of pitch

This section replaces the old Phase 5. Sections 1–8 below are the history of how we got here.

### 0.0 Pending work (consolidated, 18 Oct)

Priorities follow the product framing in 0.9: **lead with planning mode, then operations mode**. Egress only until after the demo; ingress comes last (0.10).

**Acceptance deliverables (18 Oct)**
- [ ] Source code pushed to GitLab (user). `.gitignore` now excludes runtime state (audit log, live twin layer, session caches).
- [x] Documentation with diagrams:
  - `README.md`
  - `docs/ARCHITECTURE.md` (14 Mermaid diagrams, all validated by rendering)
  - `docs/USER_GUIDE.md`, `docs/API.md`, `docs/DATA_AND_RESULTS.md`
  - SVGs in `docs/diagrams/`
  - Solution document for upload: `docs/ODT-CC_Solution_Documentation.docx` (+ `.pdf`), 29 pages, section 4.1 "Architecture layers", 18 figures (14 diagrams + 4 screenshots), 17 tables
  - "Context for judges" section in `docs/DEMO_SCRIPT.md`
  - `tests/test_dashboard_e2e.py`
- [ ] Under-5-minute demo video: script in `docs/DEMO_SCRIPT.md` (4:35: planning → live → unscripted incident → re-plan → audit trail); record it (user)

**Must, before the demo**
- [ ] Final click-through test in Kit (checklist 0.4b), done by the user with a real mouse
- [ ] Backup video: pick the tool (Kit Movie Capture, ffmpeg with sudo, or record from the laptop) and record one full run
- [ ] Rehearse the 3-minute script (0.5) twice, including the fallback (stop the NIM → saved card or template)
- [ ] Pitch deck (1 h):
  - problem
  - two modes and five modules (0.9)
  - live demo
  - architecture and NVIDIA stack
  - what it can and can't predict
  - assumptions register (0.8)
  - target architecture vs. demo build (0.11, W4)
  - production data sources
  - Ops-as-a-Service
- [x] Re-warm the fallback cards after the scoring change (`scripts/warm_caches.sh`, done 18 Oct). Normal is now: approve by min 14, peak 0.98.

**Should, to complete the product modules (in this order, if time before the freeze)**
- [x] **S1. Planning mode: compare plans side by side** (module 1). Build order: S1 → S2 → S3 (18 Oct, 2 h budget)
  - [x] Server `POST /compare {plans: [{name, plan}], minute, attendance}`. It always adds the baseline row ("do nothing" or "keep current"). Per plan:
    - verdict, dangerous zone-minutes, worst bottleneck (zone + peak)
    - zone-minutes above the alert line
    - stadium clear time, transit load (trains/hour, extra buses), effort, approve-by
  - [x] Planning input: **tickets sold** (40,000–80,000) applied to the scenario for the comparison
  - [x] Panel "Compare plans" inside "Your plan":
    - Save as A / Save as B (from the sliders)
    - Add AI plan (from the card, or fetch one)
    - a tickets-sold slider and a **Compare** button
    - a table (rows = metrics, columns = plans) and peak-density bars against the danger line
  - [x] Check that the numbers match `/simulate` for the same plan
- [x] **S2. Decision log / audit trail** (module 5)
  - [x] Server writes `data/audit_log.jsonl`:
    - scenario switch, reset, incident reports
    - recommendation (source, pick, option scores)
    - approval (which option, AI pick or the operator's own plan)
    - keep current plan, plan comparisons
  - [x] `/state` returns the last entries; `POST /decision/keep` logs "keep current plan"
  - [x] Panel "Decision log" section
- [x] **S3. Forecast confidence** (module 3)
  - [x] `/state` per zone: confidence high / medium / low (sensor ok / missed minute / offline), and an error band from the forecaster's held-out error (busy zones ±0.29, otherwise ±0.08; doubled when offline)
  - [x] Panel zone table: "4.64 ±0.3" and an H/M/L column

- [x] **S1b. Planning inputs beyond tickets sold** (18 Oct; the gap from 0.9). In "Compare plans", a "Planned event" block applies to every plan compared:
  - [x] **Staggered session end:** a share of the crowd held for a second wave, leaving after N minutes. The simulator holds those seats until then (`venue["release_waves"]`).
  - [x] **Overlapping session:** a nearby event's crowd and the minute it lets out (its people head for the station walkway over 20 min)
  - [x] **Transit timetable:** planned train frequency (2–8 min) and people per train (600–1,200). A plan's own train lever still overrides it.
  - [x] **Road closures** (no traffic model, so a closure acts through the capacity it removes): Vermont Ave → parking exits at 40%; Exposition Blvd → shuttles load at 50%
  - [x] **"Run this event live":** the planned event becomes the operations scenario ("Planned event"), linking planning mode to operations mode
  - **Results:**
    - 75,000 tickets, 30% held 15 min, trains every 5 min × 900, a 12,000-person event letting out at min 15, Vermont Ave closed: Do nothing **184**, "double the trains" 55, AI plan **SAFE** (peak 4.3, clears min 93)
    - Run live, the sensors read 74,951 scanned, the alert fires at min 2, and Nemotron's card goes 184 → 0, approve by min 6
    - Out-of-range inputs are rejected (422)
    - Panel verified by screenshot

**S1–S3 results (18 Oct):**
- **S1 Compare:**
  - 0.28 s for 3 plans; the numbers match `/simulate`
  - normal, minute 0: Do nothing UNSAFE 99, "double the trains" UNSAFE 22, AI plan SAFE (peak 0.35, 56 extra buses, clears min 87)
  - 80,000 tickets: do nothing 151 dangerous minutes, AI plan still SAFE
  - the table and peak bars render in Kit; each plan column has its own Approve button
- **S2 Decision log:**
  - logs scenario switches, resets, incidents and updates, recommendations with option scores, approvals (labelled "the AI pick", "option X (not the AI pick)" or "the operator's own plan"), "kept the current plan", and comparisons
  - written to `data/audit_log.jsonl`; the newest 8 are in `/state` and the panel's "Decision log"; `GET /audit` returns up to 50
- **S3 Confidence:** H/M/L plus ± per zone. Busy zones ±0.29, quiet ±0.08, ×1.5 after a missed reading, ×2 when sensors are offline. Verified in Kit with the sensor outage: walkway "no data · 3.00 ±0.6 · L".
- **Panel fix:** horizontal scrolling is off, so wide rows (comparison table, button rows) fit the panel instead of pushing columns off-screen.

**Could (only if everything above is done)**
- [x] C1. Stage 2 fast detection of *unreported* incidents (started 18 Oct)
  - [x] World agent: about half of nearby-event crowd surges arrive **unreported**; Inject gets a "nearby event (unreported)" choice
  - [x] Detector in `/state` (live runs): compare each zone's sensor estimate with the command centre's expected density (its own simulation of the reported situation and the plans in force). A build-up above expected by more than max(0.5, 30%) for 3 minutes in a row gives a detection.
  - [x] Detection: estimate the extra crowd per minute from how fast the gap grows, and add it to the command centre's view as an assumed inflow (typical 15 min). Report source "ODT-CC anomaly detection (sensors)", status new, so it pauses and re-plans. Logged in the decision log.
  - [x] Headless test: an unreported surge is detected within a few minutes, with no false detections on a normal run
  - **Result (18 Oct):**
    - an unreported BMO surge (750/min, walkway) injected at minute 10 was **detected at minute 14**, estimated at 500/min (the sensor average lags), and re-planned
    - a second detection at minute 31 caught the surge outlasting the estimate
    - **0 false detections** in 3 random worlds (6 reported incidents, including a rail failure that outlasted its estimate)
    - Inject offers "nearby event unreported"; the decision log records detections
- [ ] C2. Anchor E Line headway and Coliseum capacity to public data (0.8). Re-tune and re-warm afterwards.
- [ ] C3. Nemotron reviews the operator's own plan
- [x] C4. Role-based views (started 18 Oct): a "View as" selector (everyone / LA Metro / LA28 Venue Ops / LA28 Transport / LADOT / LAPD) in the panel header. Card and option actions are filtered to that agency's tasks, with a "your tasks" heading.
- [ ] C5. Lighting and camera polish; path strips coloured by flow
- [ ] C6. After-action replay of a logged run (needs S2)

- [x] W1. Web dashboard (started 18 Oct): `web/dashboard/` served at `/dashboard`; no build step, no external libraries
  - [x] Server:
    - clock tracking: Kit's `/state` calls set the minute; `client=dashboard` for standalone
    - `GET /snapshot` (clock, Kit alive, state, latest card)
    - command queue for Kit (`POST /command`: play / pause / reset / scenario / approved), delivered in Kit's `/state`
    - shared latest card (`card_id`, `GET /card`)
  - [x] Kit panel: run queued commands; pick up a card created from the dashboard
  - [x] Dashboard page:
    - header: clock, mode, Play / Pause / Reset, scenario, View as
    - banners and countdown
    - zones (estimate, +15 ± confidence, band bars); sensors
    - incident feed + Inject
    - card + 3 scored options + Approve / Keep / Recommend
    - Your plan + Simulate
    - planned event + compare table and bars + Run live
    - decision log
  - [x] Standalone mode (no Kit, or `?standalone=1`): the dashboard's own clock and pause rules
  - [x] Self-test with headless Chrome (Playwright): standalone run, then following Kit (dashboard Play → Kit plays → Kit pauses → card in the dashboard → Approve → Kit resumes)

  - **Result (18 Oct): built and self-tested.** Open **http://localhost:8000/dashboard/** on the Launchpad desktop (any browser that can reach port 8000 works).
    - The automated browser test (Playwright + Chrome) covers 11 checks:
      - Play → auto-pause at minute 3 → Nemotron card with scored options → Approve → resumes
      - decision log labels the approval
      - Live: Inject a rail failure → re-plan against "keeping the current plan" → approve a non-AI option, logged as such
      - planning: "trains every 2" UNSAFE, then Plan A vs AI comparison with planned-event inputs
    - **Standalone: 11/11 every run.** **Following Kit** (the dashboard drives Kit's timeline): the last 6 runs gave 5× 11/11 and 1× 10/11 (one check, no error). 0 server errors, 0 Kit errors.
  - **Bugs the self-test found and fixed:**
    - a sensor estimate could be NaN when the very first reading was missed (`/state` crashed, about 2% chance per zone)
    - detector false alarm after a plan change (now compares 3-minute-smoothed expectations with the 3-minute-smoothed sensors; 0 false detections with approvals at minutes 3, 8, 13, 20 plus a second plan change)
    - Kit ran dashboard commands in parallel (a reset overtook a play)
    - a card computed before a reset became the new run's card (now marked stale everywhere)
    - Kit applies timeline changes a frame late, so a reset could be undone by a stale minute (now committed immediately, minute 0 set up front)
    - Kit kept playing when a new incident arrived during a recommendation (now always pauses and refreshes the card)
    - dashboard clicks during another action were dropped (now queued)
    - re-rendering every 0.5 s could swallow clicks (only changed sections redraw; same for the Kit panel heartbeat)
  - **Known limit:** use one clock at a time. With Kit running, the dashboard follows Kit; `?standalone=1` is for running without Kit. Running both clocks at once makes them fight over the same world.

**After the demo and video recording:**
- ingress (0.10)
- W2: Omniverse streaming to the browser (0.11)
- W3: SUMO road traffic (0.11)

**Slides only:**
- access controls
- real data connectors (ticketing, GTFS, traffic)
- traffic simulation
- fire and evacuation modelling, EMS routing
- weather and heat
- multi-city service

### 0.1 Where we are against the original pitch

The original plan asked for one end-to-end slice: a post-event egress surge, with synthetic data → live twin → prediction → recommended action → operator approves. **That loop is built and works.** We took the original plan's "ambitious" option (Omniverse instead of a web map), and it runs. The gaps are elsewhere:

| Original capability | Where we are | Sprint action |
|---|---|---|
| Live city mirror: 3D venue and surroundings, live feeds | Schematic Coliseum in Omniverse. The forecaster reads **perfect densities straight from the simulator**, which no real command centre has. | **Synthetic sensor layer** (P1): ticket scans, Bluetooth zone counts, train GPS events. The system works from the estimate, not the truth. Real map: cut. |
| Predictive simulation and what-ifs | ✓ Simulator, 15-min XGBoost forecast, optimizer (2,250 plans in about 1 s). **Only one situation: a normal exit.** | **Incident scenarios** (P1) |
| Unified command view for LAPD, Fire, Metro, LA28 | One operator panel. Actions don't say who carries them out. | **Agency tag on every action** (P1, small) |
| Alerts, recommended actions, human approval | ✓ Nemotron card in 3.5 s, number guardrails, Approve. **The operator can't try their own idea.** | **"Your plan" mode:** the operator proposes, the simulation scores it (P1) |
| A twin that shows what's happening | Pillars and colours only. The plan's real cost (42k people held in the bowl) is invisible. | **Bowl fill, trains, incident marker** (P1) |
| Cyber layer, energy grid, cell/Wi-Fi, traffic (SUMO), camera vision, multi-city service | Not built | **Slides only**, as the original plan said |

### 0.2 Sprint goals, in priority order

1. **"Your plan" mode** (P1). The operator sets the 5 levers and the simulation scores the plan: SAFE / MARGINAL / UNSAFE, compared with the AI's options.
2. **Agency tags** (P1): each action names its owner.
3. **Incident scenarios** (P1). Ready-made problems the operator can pick: Expo Line delay, BMO Stadium event, walkway sensor outage. Plus a **custom incident builder** (3b) so operators can invent their own.
4. **Synthetic sensor layer** (P1). Ticket scans, Bluetooth zone counts and train GPS events are generated from the simulator. The forecaster runs on the *estimated* densities.
5. **Visuals that tell the story** (P1): bowl fill level, trains arriving, incident beacon.
6. **Demo hardening** (P1, must): one-command start, warmed-up caches, backup recording.

**Cut on 18 Oct:** the real OpenStreetMap map and the heat-surge scenario; the sensor layer took their time.

### 0.3 Work items

**1. "Your plan" mode (operator proposes, simulation scores)**
**Status (18 Oct, done):**
- `/simulate` and `/levers` are in the server.
- The "Your plan" section is in the panel:
  - sliders loaded from `/levers`
  - Simulate, "Copy AI plan" and Defaults buttons
  - a result card with the verdict banner
  - "Approve my plan", disabled when the verdict is UNSAFE
- The top button is renamed "Approve AI plan".
- Verified in Kit: "trains every 2 min" shows UNSAFE, 22 dangerous minutes, walkway at minute 18. The verdicts below were checked through the server.
- Still to do: click through Simulate and Approve with a real mouse.

Also fixed along the way:
- Server restarts no longer rewrite `twin_live.usda` (that caused Kit's "fetch changes" prompt).
- The panel turns on Kit's silent sublayer auto-reload.
- The panel retries every 2 s while the server is offline.
- Kit's font has no "→ • ✓", so panel text is ASCII apart from "²".

- [x] Server: `POST /simulate {plan, minute}` returns:
  - the score, cost and approval deadline
  - a verdict: **SAFE** (0 dangerous minutes, peak < 4.5), **MARGINAL** (0 dangerous minutes, peak ≥ 4.5) or **UNSAFE** (N dangerous minutes, naming the first zone and minute)
  - the optimizer's 3 options for comparison

  It reuses `scenarios.run`, `score`, `cost` and `approval_deadline`. One simulation takes about 7 ms.
- [x] Panel: 5 sliders (within `LIMITS`), a **Simulate** button, a result card, and **Approve my plan** (same `/approve`)
- [x] Done when: the verdicts match these checked numbers (approved at minute 2):
  - Default levers: UNSAFE, 99 dangerous minutes
  - "Trains every 2 min" alone: UNSAFE, 22 dangerous minutes, walkway at 6.0
  - AI's `most_margin`: SAFE, peak 1.18
- [ ] (stretch) Nemotron reviews the operator's plan: "safe but thin margin at…"

**2. Incident scenarios**
**Status (18 Oct, done except the sensor-outage effect and the click-through):**

Do nothing vs. the optimizer, approved at minute 2:

| Scenario | Do nothing | Safe plans | AI pick (most margin) |
|---|---|---|---|
| Normal | 99 dangerous min | 1,048 | peak 1.18, approve by min 12 |
| Expo Line delay | 129 | 510 | **shuttle-heavy:** shuttles 300, 80% to shuttles; approve by **min 6**; Nemotron card in 4.1 s |
| BMO event | 112 | 471 | trains every 2, shuttles 375, 80% to shuttles; approve by min 10 |
| Sensor outage | 99 | same as normal | Needs 4b; physically identical to normal until the sensor layer exists |

- **Demo beat:** the normal-day AI plan is **UNSAFE under the Expo Line delay** (3 dangerous minutes on the platform at minute 24). The incident changes the right answer.
- The panel has the dropdown (verified in Kit) and the incident banner. Switching a scenario resets the run.
- The panel cancels its background tasks on hot reload. An old instance's task had crashed after a reload.
- Caches are per scenario: `data/optimizer_result_<id>.json`, `data/agent_cache_<id>.json`.
- CLI: `python -m odtcc.optimizer --minute 2 --scenario expo_line_delay`, and the same `--scenario` flag on `odtcc.agent`.

- [x] Simulator: `venue["incidents"]`, read inside `simulate()`. Because it lives in the venue, the optimizer workers, `approval_deadline` and the agent's `Facts` pick it up automatically. Three incident types:
  - `capacity`: a node's throughput × factor between two minutes
  - `inflow`: extra people/min arriving at a node
  - `surge`: more attendance, faster stadium exit
- [x] `config/scenarios.json` (sizes to tune, see below):
  - `normal`: today's run
  - `expo_line_delay`: E Line signal failure, **no trains from minute 10 to 25**
  - `bmo_event`: BMO Stadium (also in Exposition Park) lets out, **about 400 people/min onto the station walkway, minutes 15–35**
  - `sensor_outage`: the walkway's Bluetooth counters go dark from minute 5. The crowd is the same as `normal`; the test is whether the system still raises the alert from the sensors around it. Needs item 4b.
- [x] Server: `GET /scenarios` and `POST /scenario {id}`. Switching re-simulates the baseline, rebuilds `crowd_live.usda` and clears any approval. `/state` gains an `incident` field (active or upcoming).
- [x] Optimizer cache keyed by (minute, scenario). Agent facts and prompt include the incident text, so the briefing mentions it.
- [x] **Must fix:** when no plan is fully safe, `optimizer.pick_options` returns nothing and the agent would crash. Return the 3 least-dangerous plans with a `no_fully_safe_plan` flag, and have the card say so.
- [x] Tune each incident so that (a) doing nothing is clearly worse than normal and (b) the optimizer still finds a safe plan, ideally a *different* one (e.g. the Expo Line delay should favour shuttles)
- [x] Panel: scenario dropdown at the top (switching = Reset), plus a red incident banner while the incident is active
- [x] Known limit, say it honestly: the forecaster wasn't trained on incidents and only reacts once densities rise. (P2) Retrain with random incidents in the training mix; it takes about 23 s.
- [ ] Done when: each scenario runs Play → alert → card → Approve → 0 dangerous minutes (user tests at the end)

**3b. Custom incident builder (added 18 Oct)**

The operator invents a problem in the panel and the system plans for it. This answers "is it just a script?"

- [x] Server: `POST /scenario` also accepts `{"id": "custom", "incidents": [...]}`. Check that the type, zone, minutes and size are within range, and generate a plain description (e.g. "Custom: Expo Park/USC platform runs at 0% capacity from minute 10 to 25").
- [x] Caches: optimizer and agent results also record the incidents, so a cached result is only reused for the *same* custom incident
- [x] Panel: "Incident builder" section (collapsed by default) with:
  - type: capacity drop / extra crowd / sensor outage
  - zone
  - start minute and duration
  - size: remaining capacity % for a capacity drop, people/min for extra crowd
  - an **Apply incident** button, which resets the run with the custom scenario
- [x] Scenario dropdown shows "Custom incident" while one is active
- [x] Sanity-check a few custom incidents through the API: a safe plan is found, or the no-fully-safe-plan fallback kicks in

  **Results (18 Oct):**

  | Custom incident | Do nothing | AI's best |
  |---|---|---|
  | No trains, minutes 8–30 | 143 dangerous min | safe, approve by min 4 |
  | +600/min into the north plaza | 191 | safe, approve by min 10 |
  | Walkway at 30%, minutes 3–60 | 97 | safe |
  | No trains all evening plus +1,000/min on the walkway | 309 | **no fully safe plan**: best cuts it to 218, and the card says "approve now to limit the danger" |

  - Bad input (unknown type or zone, out-of-range size, no incidents) gets a 422 with a clear message.
  - Nemotron's briefing names the custom incident.
  - The builder renders in Kit (verified by screenshot); clicking Apply is for the user's end test.

**3. Visuals (written into the crowd layer per simulation, so Approve and Reset keep working)**
- [x] Bowl fill: a disc inside the bowl whose height follows people still inside. It drains by minute 28 with do-nothing and is still 42k at minute 30 with the plan, so the trade-off becomes visible.
- [x] Trains: a train block slides into the platform on each departure minute, and none run during the Expo Line delay
- [x] Incident beacon: a red pulsing marker over the affected zone during the incident window
**Status (18 Oct):**
- **Bowl fill:** a blue disc on top of the bowl, 40 m tall when full. With do-nothing it's gone by minute 30; with the plan it's still 16.6 m at minute 30.
- **Trains:** a train block shows at the platform on each departure minute (4, 8, 12…). During the Expo Line delay there are none between minutes 8 and 28.
- **Incident beacon:** a sphere over the zone that pulses every other minute; red for capacity and inflow incidents, amber for sensor outages.
- All three are animated in the crowd layer from each simulation, so Approve, Reset and scenario switches update them. Verified in the USD data, and the bowl disc in the Kit viewport.
- The colours in the viewport are fine. Earlier "grey" screenshots were a 24-bit capture bug in my screenshot tool, not Kit.
- Trains and the beacon were only checked in the USD data, not watched playing in Kit (part of the user's final test).

- [ ] Lighting and camera tuned by eye; Kit window maximized
- [ ] (P2) Path strips coloured by flow, red where flow is blocked

**4. Agency tags**
- [x] Each lever names its likely owner, and each card action is prefixed with it:

  | Lever | Owner tag |
  |---|---|
  | Release from bowl | LA28 Venue Ops |
  | Trains | LA Metro |
  | Shuttles | LA28 Transport / LADOT |
  | Steering crowds | Venue stewards / LAPD |

  Example: "[LA Metro] Run a train every 2 min". About 20 min of work.

**5. Demo hardening**
- [x] `scripts/demo_up.sh`: check or start the NIM → start the server → wait for `/health` → launch Kit
- [x] Warm the caches at the minute each scenario pauses (normal 3, Expo Line delay 2, BMO 2, sensor outage 4) for every scenario (`optimizer --minute N --scenario X` and `agent --minute N --scenario X`), so the offline fallback works
**Status (18 Oct):**
- `scripts/demo_up.sh` starts the NIM (waits up to 5 min), then the server, then Kit. Each step is health-checked and skipped if already running.
- `scripts/warm_caches.sh` saves an optimizer result and a Nemotron card per scenario (normal min 3, Expo Line min 2, BMO min 2, sensor outage min 4). All four cards took 1.6–3.0 s and recommend most margin.
- **Automatic fallback chain:** if live Nemotron fails, the server replays the saved card for the same minute and incident (source `cached:nemotron-local`, with the reason); otherwise it uses the template. Tested with the NIM unreachable.
- Each scenario's cache holds **one** minute. Running `python -m odtcc.agent --minute N --scenario X` by hand overwrites it, so re-run `warm_caches.sh` before the demo.

- [ ] Backup video. The box has no ffmpeg or screen recorder. Use Kit's Movie Capture extension, install ffmpeg (needs sudo), or record from the laptop showing the remote desktop. **Decide at H0.**
- [ ] Rehearse twice, including the fallback (stop the NIM → template card)

**4b. Synthetic sensor layer (hidden truth → sensors → estimate)**
- [x] `odtcc/sensors.py`, `observe(sim, venue, seed)`, turns the simulator's truth into what real feeds would report:
  - [x] **Ticket scans:** people who entered, as an almost exact count. This is where "70,000" really comes from.
  - [x] **Bluetooth/Wi-Fi counters per zone:** each person's phone is detected with about 30% probability, with ±8% noise from ID randomization and about 2% random missed minutes. Counts are aggregated, never individuals.
  - [x] **Sensor outages:** `sensor_outage` incidents take a zone's counters offline for their window
  - [x] **Train GPS (GTFS-realtime style):** departure minutes at Expo Park/USC, so the Expo Line delay shows as missing trains
- [x] Estimator: count ÷ detection rate ÷ area, smoothed over 3 minutes. A missed minute carries the last value forward. A dead zone is marked "offline", and its density is left for the forecaster to infer from neighbouring zones.
- [x] Forecaster retrain: inputs come from the **estimates** (with random outages); the target stays the **true** density 15 min ahead. 30% of training runs also get a random capacity or inflow incident. About 23 s.
- [x] Check the alerts: normal still alerts around minute 2; with the sensor outage the walkway is still flagged, from its neighbours; record recall and precision
- [x] `sensor_outage` scenario starts at minute 0 (walkway counters out of service at the final whistle). An outage from minute 5 would land after the minute-2 alert and show nothing.
- [x] Server: `/state` uses estimates for "now" and for the forecast, and adds a `sensors` block (Bluetooth online/total, ticket scans, last and next train). The 3D twin keeps showing the simulated truth.
- [x] Agent: `Facts.forecast` uses the same sensor-based forecast as `/state`
- [x] Panel: sensor strip (e.g. "Bluetooth 9/10 online | 70,012 scanned in | last train min 8"), an "est." tag on densities, and "no data" for offline zones
- [x] Traffic sensors (ATSAC, PeMS): slide only, since our model is pedestrians

**Results (18 Oct):**
- **Sensor accuracy:** estimated density is off by 0.07 p/m² on average, and about 6% in busy zones. The 3-minute average lags a growing crowd (walkway: 4.55 estimated vs 5.06 true at minute 18).
- **Retrained forecaster** (33 s on GPU, 600 runs, 30% with random incidents and 30% with random outages):
  - catches **97%** of dangers (99% before, on perfect data)
  - 83% of alerts are real
  - average error when busy: 0.29 (0.17 before)
  - after approval only **2** false alarms (6 before)
  - old model backed up in the scratchpad; `models/` is in git
- Alert timing from sensor data, doing nothing:

  | Scenario | First alert | Warning before danger |
  |---|---|---|
  | Normal | **minute 3** (was 2) | walkway 15 min, platform 16 |
  | Expo Line delay | walkway 2, platform 4 | 11–20 min |
  | BMO event | walkway 2 | 15 min |
  | Sensor outage | platform 4; **walkway 6 from neighbouring zones alone** | walkway 12 min |
- **Demo impact:** the normal run now pauses at **minute 3**, which matches the original "approve at minute 3" story.
- **What stays on the simulator's truth:** the 3D twin's pillars (they show reality), and the optimizer's what-if runs, which re-simulate from minute 0. Production would start them from the estimated state ("data assimilation"); say so if asked.
- Panel verified in Kit with the outage scenario: "Bluetooth 9/10 online | 69,870 scanned in", walkway "no data", incident banner.

**6. Live incidents: Plan A, stage 1 (added 18 Oct, about 2 h)**

Goal: problems aren't predefined. A **world agent** injects realistic incidents at random moments during playback, the command centre re-plans **from that minute on top of the plan already in force**, and offers **3 scored options**. The preset scenarios stay as the rehearsed backup.

- [x] **Event-log world:** `simulate()` takes a list of plan phases (each approval adds one), so plans can change several times. Re-simulating from minute 0 with the log must reproduce the past exactly, because incidents only act from their start minute; test this.
- [x] Optimizer, `approval_deadline`, robustness, `/simulate` and `Facts` take the plans already in force (`base_plans`). "Do nothing" becomes "keep the current plan" once something is approved.
- [x] **World agent** (`odtcc/world.py`): a catalog of realistic incidents with odds, sizes and durations:
  - Expo Line signal failure
  - trains at reduced frequency
  - medical emergency on the walkway
  - crowd from a nearby event (BMO Stadium, Natural History Museum)
  - shuttle breakdowns
  - Bluetooth sensor failure

  It may strike during minutes 5–45 (at most 2 incidents, at least 8 minutes apart), plus an **"Inject incident now"** button for demo control.
- [x] **Truth vs belief:** the incident's real duration is hidden. The command centre gets a *report* (e.g. "LA Metro: E Line signal problem, trains held") and plans with the catalog's *typical* duration. The 3D twin shows the truth.
- [x] Server:
  - a new `live` scenario (normal egress plus the world agent)
  - world ticks as `/state` reaches each new minute
  - `POST /incident/inject`
  - an approval log, so `/approve` appends a phase
  - `/state` lists reported incidents
- [x] **Scored options:** each of the 3 options gets a 0–100 score with a visible breakdown (safety margin, clear time, effort, time left to approve). Computed by code; Nemotron explains.
**Status, backend (18 Oct):**
- **Event-log world:** re-simulating with an extra incident or approval leaves the past bit-identical. Tested, and the baseline CSV still matches.
- **World agent:** about 1.8 incidents per run, the first usually around minute 12. It never starts a second copy of an incident that's still running.
- **Follow-up reports:** "still ongoing" (the belief is extended by 5 min) and "cleared" are shown in the feed; only *new* incidents pause.
- **Scoring counts danger from the decision minute on**, so re-plans aren't penalised for the past. The first plan now has peak 1.35.
- **Scores:** 0–100 with parts: safety 45, speed 20, ease 20, time to act 15. A normal run gives 35 / 65 / 38.
- **Card states:**
  - "no change needed" when the current plan is still the best
  - **ESCALATE** when nothing beats the current plan and danger is still expected
- **Headless live runs** (each with a Nemotron card in 3.5–6 s):
  - seed 5: a BMO surge on the walkway (750/min) gives "current plan 3 dangerous → re-plan 0"; then a rail failure on top, where nothing helps, gives ESCALATE
  - seed 21: a crowd at the north plaza is absorbed; then a train shortage gives "current plan 6 → re-plan 0"
- **Demo tip:** inject "nearby_event" or "rail_signal_failure" for a visible re-plan. Medical and shuttle incidents are usually absorbed, which is realistic but quiet.

- [x] Panel:
  - "Live: random incidents" in the dropdown, and an Inject button
  - auto-pause on each **new incident** (not just the first alert)
  - repeated approvals
  - 3 option cards, each with its score and an "Approve this option" button
- [x] Headless test of a full live run through the API: incident → re-plan → approve → second incident → re-plan
**Status, panel (18 Oct):** verified by screenshot with a crowd injected at minute 3:
- "Live: random incidents" with its description, the incident feed, and the Inject selector (random or a specific type)
- the card names the incident; before/after shows 135 → 0
- 3 option cards with score breakdowns and an "Approve this option" button each
- **Pause rules:** a new incident report always pauses; a newly alerting zone pauses only 5+ minutes after the last decision; "still ongoing" and "cleared" just appear in the feed
- **Approvals repeat:** each card can be approved once; "Keep current plan" dismisses a card; "No change needed" disables Approve
- **Clicks need the user's mouse** (final test)

- [x] Stage 2 (done as C1 in 0.0, 18 Oct): detect *unreported* incidents from sensor data (zone rising faster than expected, missing train departures) instead of being told

### 0.4 Timeline (6 h)

| Time | M: server, simulator, agent | T: Kit panel, twin |
|---|---|---|
| 0:00–0:15 | Agree on scope, decide the backup-video tool, Reset everything | same |
| 0:15–1:00 | `/simulate`, verdicts, agency tags | "Your plan" sliders and result card (mock JSON first) |
| 1:00–2:15 | Incidents in the simulator, scenarios, optimizer no-safe-plan fix, agent incident facts, tuning | Scenario dropdown and incident banner, then bowl fill and trains in `build_twin` (coordinate: shared file) |
| 2:15–3:45 | Sensor layer, estimator, retrain the forecaster on the estimate, `sensor_outage` scenario | Incident beacon, "Sensor feeds" strip, lighting, camera |
| **3:45** | **Checkpoint:** do all scenarios run the full loop in Kit? | |
| 3:45–4:30 | `demo_up.sh`, warm caches, edge cases | Fix whatever the checkpoint found |
| 4:30–5:15 | Integration and two full rehearsals | same |
| 5:15–6:00 | Record the backup video. **Feature freeze at 6:00.** | same |
| +1 h | Pitch deck | Pitch deck |

### 0.4b Final test checklist (user, at the end)

Start with `scripts/demo_up.sh` (everything is already running right now), then `scripts/warm_caches.sh`. Maximize the Kit window.

- [ ] **Normal:** press Play. It pauses at **minute 3** (amber walkway warning), and the card shows `nemotron-local`, approve by minute 14.
- [ ] **Your plan:**
  - set "Train every" to 2 → **Simulate** → UNSAFE, 22 dangerous minutes, approve button disabled
  - **Copy AI plan** → Simulate → SAFE
- [ ] **Approve** (AI plan or "Approve my plan"):
  - banner turns green, playback resumes, before/after shows 99 → 0
  - the walkway and platform pillars stay low around minutes 15–25
  - the blue bowl disc drains slowly (people held inside)
- [ ] **Reset:** back to minute 0, and the twin is back to do-nothing.
- [ ] **Expo Line delay** (dropdown):
  - pauses at minute 2; the shuttle-heavy card says approve by minute 6
  - from minute 10 the red INCIDENT banner and the beacon over the platform appear
  - no train at the platform between minutes 8 and 28
- [ ] **BMO Stadium event:** pauses at minute 2; the card says approve by minute 10.
- [ ] **Walkway sensor outage:**
  - sensor strip turns amber, "Bluetooth 9/10"; the walkway shows "no data"
  - pauses at minute 4 (platform); the walkway gets flagged around minute 6
- [ ] **Incident builder:** expand it, pick "Extra crowd arrives", North plaza, start 5, 20 min, 600/min → **Apply**. The dropdown shows "Custom incident" and Play gives a card naming the incident.
- [ ] **Live: random incidents** (dropdown), press Play:
  - pauses at minute 3, approve
  - incidents arrive by themselves, on average about 2 per run with the first around minute 12; each new report pauses and gives a **Re-plan** card
  - the before/after table says "Keep current"; the 3 option cards show scores; try "Approve this option" on a non-AI pick, and "Keep current plan"
  - "still ongoing" and "cleared" updates appear in the feed without pausing
- [ ] **Inject incident now** with "nearby event" or "rail signal failure" after the first approval: it should show "keeping the current plan → N dangerous minutes" and a re-plan to 0 (or ESCALATE if nothing helps)
- [ ] Reset in Live gives a new random world (different incidents)
- [ ] **Compare plans** (planning mode, minute 0, scenario normal):
  - set sliders to "trains every 2" → Save as Plan A; Copy AI plan, or "Recommend now" then Add AI plan
  - Compare → table + bars: Plan A UNSAFE 22, AI SAFE; move "Tickets sold" to 80,000 and Compare again
  - "Approve" from a column works
- [ ] **Planned event inputs:** set 2nd wave 0.30 after 15 min, nearby event 12,000 at min 15, trains every 5 × 900, Vermont closed → Compare. Do nothing ~184, AI SAFE. Then **Run this event live** → the dropdown shows "Planned event"; Play pauses at min 2 with a card for the planned conditions.
- [ ] **Unreported incident:** in Live, after the first approval, Inject "nearby event unreported". About 3–4 minutes later the feed shows "ODT-CC anomaly detection (sensors): Unreported crowd build-up…", and it pauses and re-plans.
- [ ] **Decision log:** shows scenario switches, recommendations with scores, approvals labelled "AI pick" / "operator's own plan", "kept current plan", detections. The file is `data/audit_log.jsonl`.
- [ ] **Confidence column:** H everywhere normally; L and "no data" on the walkway in the sensor-outage scenario
- [ ] **View as:** LA Metro → the card shows "Your tasks (LA Metro)", and option cards show only Metro actions
- [ ] **Fallback:** `docker stop nemotron`, Reset, Play. The card source is `cached:nemotron-local` (or `template`). Then `docker start nemotron`.
- [ ] Decide on the backup video tool (Kit Movie Capture, ffmpeg with sudo, or record from the laptop) and record one full run.

### 0.5 Demo script v3 (3 minutes): planning mode first, then operations

Numbers as of 18 Oct (after the scoring change).

1. **0:00 Problem.** 70,000 people leave the Coliseum at once, and the Expo Park/USC station is the chokepoint. Individuals are unpredictable, crowds aren't.
2. **0:20 Planning mode** (before the event; timeline at minute 0, nothing playing):
   - Scenario *normal*. In "Your plan", try "double the trains": **UNSAFE, 22 dangerous zone-minutes, walkway at 6.0**.
   - Copy the AI's plan → **SAFE**. With S1 built, show Plan A vs Plan B vs AI side by side instead.
   - Switch to *Expo Line delay* (a *known* disruption, like planned maintenance). The normal-day plan is now UNSAFE, and the AI's plan becomes **shuttle-heavy**. *"A bad shuttle plan found here costs nothing; found on the day it's a crisis."*
3. **1:15 Operations mode** (event day, live):
   - Switch to *Live: random incidents*. The sensor strip shows about 70,000 scanned in, Bluetooth online; densities are *estimates*. Press Play.
   - **Minute 3:** auto-pause, "Walkway approaching danger by minute 18".
   - The Nemotron card: hold the crowd at 800/min, trains every 2 min, shuttles 375/min, 70% of the north plaza to shuttles. 3 options scored 35 / **65** / 38. Approve: 99 → 0 dangerous zone-minutes, peak 6.44 → 0.98, approve-by minute 14. Trade-off: the stadium clears at minute 81 instead of 28, and the bowl visibly holds people.
4. **2:00 The incident nobody scripted.** A judge presses **Inject incident now** ("nearby event" or "rail signal failure"):
   - the report arrives from its realistic source (LA Metro alert, Exposition Park venue ops) and auto-pauses the run
   - the re-plan card shows "keeping the current plan → N dangerous minutes" and 3 scored options; approve one
   - *"The twin can't predict an incident, but it detects it on one shared screen and turns it into ranked options in seconds."*
   - Every action carries its agency tag.
5. **2:40 Close.** One platform, two modes, five modules (0.9); assumed vs. real data (0.8); the committee already holds most of the data; Ops-as-a-Service for future host cities.

Backup paths: the preset scenarios (fully reproducible), the saved Nemotron cards, the recorded video.

### 0.6 Cut (slides only)
- Real Exposition Park map (OpenStreetMap), heat-surge scenario (cut 18 Oct for the sensor layer)
- Traffic simulation (SUMO), synthetic traffic-sensor data, energy grid, cell/Wi-Fi data, camera vision, the cyber layer, multi-city service, a web dashboard
- Injecting an incident mid-run on top of an approved plan and re-planning. The simulator supports only one plan switch, so this needs a second switch. **Stretch only.**
- Ghost pillars, CuPy GPU sweep, slowed playback

### 0.7 Honest pitch lines (from the original plan)
- **Geometry:** a schematic venue (plus OpenStreetMap context if it's built). In production: city GIS, LiDAR and stadium BIM models, brought in as USD.
- **Feeds:** synthetic here, but realistic in shape: Bluetooth counts detect only about 30% of phones and are noisy, and the system works from that estimate rather than the simulator's truth. In production:
  - LA Metro's public GTFS-realtime feed
  - LADOT ATSAC and Caltrans PeMS traffic data (needs a data-sharing agreement)
  - Camera counts via NVIDIA Metropolis, as aggregate counts only with no identities (the privacy question, addressed directly)
  - Carriers' aggregated mobility data (a licensing deal)
- **Forecaster:** trained on the simulator, not on real crowds.
- **The safe plan has a cost:** it holds people in the bowl, so the stadium clears at minute 83 instead of 28.


### 0.8 Data sources: what's real, what's assumed (added 18 Oct)

Reviewed against the list of datasets LA28, the city and transit agencies already hold.

**Already covered (no work needed):**
- **Ticket scans:** the sensor layer produces the scan count that sets the "70,000"; the panel shows it.
- **Transit capacity:** train frequency and capacity are simulator inputs, and "train GPS" departures are a feed.
- **Live feeds:** Bluetooth zone counts, gate exit counters and train departures, with realistic noise and outages. This goes beyond the list, which only names static datasets.
- **Incident sources:** every world-agent report names a realistic owner (LA Metro alert, LAFD dispatch, LA28 Transport, Exposition Park venue operations, network operations), matching who would hold that data.
- **Ownership:** every action names the agency that carries it out (agency tags).

**Not adding before the freeze:**
- Ingress arrival curves from scan-in timestamps: we model the exit, not the entry.
- Accessible and hospitality ticket mix.
- Staffing rosters and screening lanes.
- LAFD and LAPD unit locations, and evacuation routes.
- Road closures and the Games Route Network.
- Weather and air quality, hotel occupancy, LAX volumes.

These all go on the "production data sources" slide.

**Assumptions register** (for the pitch, the "say what's assumed" tip). All values are from `config/venue.json` and the code:

| Input | Value used | Status | Real source in production (owner) |
|---|---|---|---|
| Crowd leaving | 70,000 at the final whistle | Assumed (the Coliseum seats about 77,000) | Ticket scan-ins per session (LA28 ticketing) |
| Exit timing | everyone starts leaving at minute 0, bowl exits up to 2,500/min | **Assumed**; real crowds trickle out early and linger | Gate exit counters, past-event egress curves (LA28 venue ops) |
| Gate split | north 35%, east 25%, south 20%, west 20% | Assumed | Seat map to gate assignment (LA28 venue ops) |
| Gate, plaza, walkway capacities | 650–750 / 1,400–1,500 / 600 people/min | Assumed | Venue layout and screening throughput (LA28 venue ops) |
| Zone areas | 800 m² gates … 10,000 m² parking | Schematic | Venue CAD/BIM, temporary fencing plans |
| Station split | 60% of the north plaza to the walkway, 40% to shuttles | Assumed mode split | Metro tap data and the LA28 mode-split plan |
| Trains | every 4 min, 1,000 people per train | **Assumed** | Metro's public GTFS feed (headways) and published car capacity (LA Metro) |
| Shuttles | 150 people/min | Assumed | Shuttle fleet allocation (LA28 Transport, contractors) |
| Danger and alert lines | 5 and 4.5 people/m² | Based on Fruin crowd levels of service | Same; standard practice |
| Bluetooth detection | 30% of phones, ±8% noise, 2% missed minutes | Assumed sensor model | Calibrated against gate counts on the day |
| Incident odds and sizes | world-agent catalog (`odtcc/world.py`) | **Assumed** | Historical incident logs from past Coliseum and SoFi events (LAPD, LAFD) |
| Forecaster | trained on 600 simulated runs | Synthetic | Retrained on historical event data |

- [x] Assumptions register (this table)
- [ ] Turn the table into a pitch slide ("assumed vs. real source"), together with the production data sources list
- [ ] (P2, **only after the final test passes**) Anchor the two most visible numbers to public data: E Line headway at Expo Park/USC from Metro's public GTFS feed, and the Coliseum's published capacity. Changing train numbers shifts every result, so it means re-tuning the incidents, re-warming the caches and checking the demo numbers again. Skip it if time is short; the register already says these are assumed.
- [ ] (cut, slide only) A realistic exit curve (some leave early, others linger) driven by session end times and scan-out data

**Pitch lines to add:**
- "The organizing committee and the city already hold most of this data. The twin mostly *joins existing datasets*: ticketing, venue ops, Metro GTFS and taps, LAFD/LAPD incident logs. It doesn't ask for new sensors."
- "We separate what's assumed from what has a real source" (show the register).
- "The world agent's incident odds would be calibrated on historical incident logs from past events at the same venues."


### 0.9 Product framing: two modes, five modules (added 18 Oct)

**What a twin can and can't predict** (a pitch slide):
- **It predicts aggregate, flow-driven behaviour well:** egress surges, where queues form at gates, plazas and stations, platform overcrowding and transit shortfalls, overlapping events, knock-on effects. That's our simulator, forecaster and optimizer.
- **It cannot predict incidents starting:** a fire, a medical emergency, a fight, a power failure. So our incidents are random (world agent), not forecast. For those, the twin does **fast detection** (one shared screen; stage 2 adds detection from sensors) and **consequence simulation** (re-plan from the moment the incident is known, with ranked options).

**Two modes, one platform:**

| | Planning mode (before the event) | Operations mode (event day) |
|---|---|---|
| Question | "What if trains are cut / we add shuttles / hold the bowl?" | "What's happening now, what happens in the next 15 min?" |
| In our build | Scenarios + incident builder + "Your plan" Simulate at minute 0 + AI options. **Missing: side-by-side plan comparison (S1).** | Live run, sensor feeds, 15-min forecast, world-agent incidents, re-plan, approvals |
| Users | Planners (LA28, LADOT, Metro) | Command-centre operators, agency liaisons |
| Demo order | **First** | Second |

**Five modules, mapped to what's built:**

| Module | Status | Gap → pending item |
|---|---|---|
| 1. Scenario planner | **Partial:** preset and custom scenarios, operator plans scored SAFE/MARGINAL/UNSAFE, AI's 3 options | Side-by-side Plan A vs B, planning inputs such as tickets sold → **S1** |
| 2. Live operations view | **Done:** 3D twin (crowd pillars, bowl fill, trains, incident beacons), sensor strip, zone table, alerts, incident feed | Role-based views per agency → C4 (could) |
| 3. Short-horizon forecasting | **Done:** 15-min XGBoost forecast from sensor estimates, refreshed every minute, alerts at 4.5 | Confidence indicator → **S3** |
| 4. Incident response simulator | **Done:** incident builder, world-agent incidents, re-plan on top of the current plan, 3 ranked options, ESCALATE | Fire and evacuation, EMS routing → slides; unreported-incident detection → C1 |
| 5. Recommendations with human approval | **Done:** Nemotron card with number guardrails, agency tags, approve or keep current plan; the agent never acts alone | Audit trail → **S2**; after-action replay → C6 |
| Supporting: data connectors | Synthetic feeds (ticket scans, Bluetooth, train departures) | Real ticketing, GTFS and traffic connectors → slides |
| Supporting: scenario management | `config/scenarios.json` + custom incidents + live seeds (replay a random world) | — |
| Supporting: access control | — | Slides |

### 0.10 After the demo and video recording: ingress (arrivals)

Only once the egress demo is recorded and the pitch is done. Everything so far models the **exit**. Ingress is the same twin running in reverse, with different bottlenecks: security screening instead of gate exits.

- [ ] Ingress simulator:
  - arrivals follow an arrival curve before session start (from ticket scan-in timestamps in production; assumed curve here)
  - people come from the station, shuttles and parking towards the gates
  - the gates' capacity is **security screening lanes × throughput per lane**, with queues forming outside the gates
- [ ] Ingress levers:
  - open more screening lanes
  - open gates earlier
  - meter the flow from the station
  - add shuttle drop-off capacity
  - Each gets an agency tag (LA28 Venue Ops, LAPD for screening, LA Metro).
- [ ] Ingress incidents for the world agent: a screening lane failure, train bunching (several trains arriving at once), a gate closed for a security sweep, a late surge before session start
- [ ] Forecaster: retrain with ingress runs (or a separate ingress model); the alert is "queue outside Gate N exceeds X minutes or Y people/m²"
- [ ] Panel and server: an **ingress / egress** mode switch; the clock counts down to session start instead of up from the final whistle
- [ ] 3D twin: queues building outside the gates, and the bowl filling instead of draining
- [ ] Full event day (stretch): ingress → session → egress as one timeline, with overlapping sessions at nearby venues


### 0.11 Target architecture vs. demo build (added 18 Oct)

**Target architecture (the product we'd deliver):**

| Layer | Target | Role |
|---|---|---|
| Simulation logic | Our Python code: crowd egress, transit, Plan A vs B, incident re-simulation; **SUMO** for road traffic | The "brain": produces the numbers and positions |
| 3D twin view | **NVIDIA Omniverse** (cloud), streamed to the browser | Renders the venue as a live OpenUSD scene: crowd heat maps, moving buses, closed roads, incident markers |
| Control and decision UI | **Web dashboard (React)** | Scenario planner, Plan A/B comparison charts, alerts, approve/reject, audit log |

Omniverse is **not** a crowd or traffic simulator. It's the 3D engine and scene platform; our simulation pushes state into the USD scene (positions, colours, densities) and Omniverse shows it. NVIDIA publishes blueprints and sample extensions for city-scale twins.

**What we built for the demo, and how it maps:**

| Layer | Demo build (works today) | Gap to target |
|---|---|---|
| Simulation logic | `odtcc/` Python: simulator, sensors, XGBoost forecaster, optimizer, world agent, Nemotron agent, FastAPI server on :8000 | **No road traffic (SUMO)**; pedestrians and transit only |
| 3D twin view | Omniverse Kit on the Launchpad GPU (local, not cloud); the server writes `usd/layers/crowd_live.usda` and Kit auto-reloads it | Cloud or browser streaming not set up. **The streaming Kit apps are already built** here (`my_editor_streaming`, `my_usd_viewer_streaming` + messaging extension) |
| Control and decision UI | omni.ui panel inside Kit (`kit/exts/odtcc.command_center`) | Not a web dashboard; no charts; audit log pending (S2) |
| Same data stream for both | **Already true:** one server; the panel and the 3D scene both update from it (an injected incident updates the alert, the card and the twin together) | — |

**Decision for the demo (18 Oct): keep the Kit panel as the control UI.** It's built, tested end to end, and shows the 3D view and the controls on one screen. The server's REST API (`/state`, `/recommend`, `/simulate`, `/approve`, `/scenario`, `/incident/inject`) is exactly what a web dashboard would call, so the dashboard is an added front end, not a rewrite. Present the web dashboard and cloud streaming as the deployment architecture on a slide.

**Follow-up items** (added to 0.0):
- [x] W1 (done 18 Oct; see W1 in 0.0). Web dashboard served by the FastAPI server (could, after S1–S3; ~2–3 h). A single page (or React) using the same endpoints:
  - scenario picker
  - metrics and alerts
  - Plan A/B comparison chart (with S1)
  - approve / reject
  - audit log (with S2)
- [ ] W2. Stream the 3D view to the browser with the built `my_usd_viewer_streaming` app and embed it beside the dashboard (after the demo; needs network ports or cloud access checked)
- [ ] W3. Road traffic with SUMO on an OpenStreetMap network around Exposition Park, with shuttle buses as SUMO vehicles and moving buses and closed roads in the USD scene (after the demo; slide until then)
- [ ] W4. Slide: the target-architecture diagram above, with the demo build marked as "running today"

**Pitch line:** *"Omniverse provides the OpenUSD digital twin and visualization layer, our prediction services run on NVIDIA GPUs, and the command dashboard is where operators plan, approve and audit decisions."* In the demo the dashboard is the panel next to the viewport; in production it's a web dashboard with the 3D view streamed beside it.

---

## 1. Decisions

| Topic | Decision |
|---|---|
| Demo surface | **Kit Base Editor + our own extension panel**. Browser streaming and the web viewer are out of scope **for the demo**; they're the production target (see 0.11, revisited 18 Oct). |
| Timing | The hackathon has started. 2 people, same split. |
| LLM | Self-hosted **Nemotron NIM on GPU 1**. The NGC key is available. Use the hosted build.nvidia.com endpoint while the image downloads, and as a fallback. |
| Approval minute | The plan takes effect **from the minute the operator clicks Approve**, not from minute 0. Everything before that minute is the "do nothing" scenario. |
| Kit GPU | Pin Kit to **GPU 0** so GPU 1 is free for the NIM. |
| Fallback | Pre-rendered MP4s and a screen recording of the full loop. |

---

## 2. The approval minute

**Definition:** the minute on the match-day clock (0 = final whistle) when the operator clicks **Approve**. Before it, the crowd follows "do nothing". From it onward, the approved plan's levers are in force.

**Measured** with `DEMO_PLAN` (release 1,200/min · trains every 3 min · shuttles 300/min · 55% to shuttles · 50% east→south). The test used a scratch copy of the simulator with a mid-run switch; with approval at minute 0 it reproduces the existing results exactly.

| Approved at minute | Dangerous zone-minutes | Peak density (p/m²) | Stadium cleared by |
|---|---|---|---|
| 0 | 0 | 3.17 | min 58 |
| **3 (demo)** | **0** | **4.34** | min 55 |
| 5 | 0 | 4.93 | min 52 |
| 8 | 38 | 6.0 | min 49 |
| 10 | 41 | 6.0 | min 47 |
| never ("do nothing") | 99 | 6.44 | min 28 |

**Demo story:** the alert fires at minute 2 and the operator approves at minute 3, giving 99 → 0 dangerous minutes. The **approval deadline** is about minute 5–7; after it, no version of this plan prevents danger.

**As built (17 Oct):** the live loop pauses at **minute 2** and the operator approves there. Nemotron recommends the optimizer's `most_margin` plan, not `DEMO_PLAN`: 99 → 0 dangerous zone-minutes, peak 6.44 → **1.18**, stadium clears at minute **83** (vs 28), approve by minute **12**. The table above is for `DEMO_PLAN`.

**How the approval minute flows through the system:**
- **Simulator:** `run(venue, plan, start_minute=t)` switches levers at minute t.
- **Optimizer:** each candidate plan is simulated from the current minute, not from 0.
- **Agent:** the briefing states the approval deadline (*"approve by minute 6 or the walkway goes red at minute 18"*).
- **Server:** `/approve` receives `{plan, minute}` and builds the live crowd layer from that minute.
- **Twin:** before t the live layer is identical to baseline, so the twin keeps playing smoothly and only diverges after the click.
- **Panel:** shows a countdown to the approval deadline next to the Approve button.
- **Predictor:** after approval, forecasts must reflect the plan now in force, so training data needs plans that start mid-run (see Phase 1).

---

## 3. Architecture

```
┌──────────────── Kit Base Editor (GPU 0, DISPLAY :1) ────────────────┐
│  Viewport: usd/twin_live.usda (layout + crowd layer [+ forecast])   │
│  "ODT-CC Command Center" panel (omni.ui extension, kit/exts/):      │
│    clock · zone table · ALERT · deadline countdown · briefing ·     │
│    [Approve] · before/after metrics                                 │
└───────────────┬──────────────────────────────────────────────────────┘
                │ HTTP localhost:8000 (urllib in an executor thread)
┌───────────────▼──────── brain service (.venv, FastAPI) ─────────────┐
│ GET  /state?minute=t      densities now + 15-min forecast + alerts   │
│ POST /recommend {minute}  optimizer sweep → top plans + deadline →   │
│                           Nemotron briefing + action card            │
│ POST /approve {plan,min}  sim switched at min → layers/crowd_live    │
└───────────────┬──────────────────────────────────────────────────────┘
                │ OpenAI-compatible API (base_url env var)
        Nemotron NIM (GPU 1)  ──fallback──▶ build.nvidia.com hosted endpoint
```

Kit's own Python can't import the venv's cuDF or XGBoost, so the extension is a thin client and the computation stays in `.venv`.

---

## 4. Status: what's done

### Step 0: Environment
- [x] Launchpad box (2× L40S, 46 GB each), project at `~/Desktop/Olympic_DigitalTwin/Nvidia`, `.venv` with cuDF 26.10, usd-core 26.8, pandas, numpy
- [x] Kit app template built (`my_editor`, `my_usd_composer`, `my_usd_viewer` and their streaming variants)
- [x] Kit Base Editor opens **with a window** on the Launchpad desktop (`DISPLAY=:1`)

### Step 1: Data and simulation
- [x] `venue.json`: 11 nodes with positions, areas, capacities and routing
- [x] `generate_data.py`: 120-minute egress simulator with density cap and back-pressure
- [x] `data/egress_baseline.csv`

### Step 2: OpenUSD twin
- [x] `build_twin.py`: layered USD (`usd/venue_layout.usda` + `usd/crowd_<s>.usda` → `twin_<s>.usda`)
- [x] Twin opens and plays in Kit Base Editor
- [x] `render_twin.py`: headless RTX frame capture (12 preview PNGs in `renders/baseline/`)

### Step 3: Scenario engine
- [x] `scenarios.py`: 5 operator levers with limits, `apply_plan`, `run`, `score`
- [x] `DEMO_PLAN` gives 99 → 0 dangerous zone-minutes (applied from minute 0)
- [x] `twin_baseline.usda` and `twin_plan.usda` built

### Step 4: Predictor
- [x] `predictor.py` written: XGBoost, 15-min horizon, `train` and `demo` commands
- [x] Trained on GPU on this box; `models/forecaster.json` and `models/forecaster_meta.json` saved

### Analysis
- [x] Repo and box audit (this file)
- [x] Approval-minute feasibility checked: approval at minute 3 still gives 0 dangerous minutes

---

## 5. To do

### Phase 0: Unblock and clean up (M, now, about 1 hour)
- [x] `pip install xgboost fastapi uvicorn openai` (xgboost 3.4.1, fastapi 0.143.2, openai 3.31.0)
- [x] `python predictor.py train`: ran on **cuda** in 23 s (504k training rows). Held-out results: recall 100%, precision 82%, mean error 0.03 p/m² overall and 0.11 when busy. This also needed `scikit-learn`, which is now installed.
- [x] `python predictor.py demo`: walkway alert at min 2 → danger at min 18 (16 min warning); platform 3 → 19 (16); north gates 6 → 26 (20); shuttle loading 19 → 37 (18)
- [x] `.gitignore` covers `.venv/`, `__pycache__/`, `renders/` and `*.log`. It used to ignore itself; it's now tracked so the teammate gets it.
- [x] Removed the stray root `egress_baseline.csv`, an outdated copy from the first commit that nothing references. Removed `odtcc_twin.usda`, which was replaced by the layered twins.
- [x] Added `requirements.txt` with pinned versions (install with `--extra-index-url=https://pypi.nvidia.com` for cuDF)
- [x] Committed everything (including `models/`, `PLAN.md`, `overview.md`, the twins and `usd/`). Not pushed yet.
- [x] `launch_editor.sh`: starts Kit Base Editor on `DISPLAY=:1`, pinned to GPU 0 (`multiGpu/enabled=false`, `autoEnable=false`, `maxGpuCount=1`, `activeGpu=0`; all confirmed to exist in this Kit build). It loads `exts/` automatically once Phase 4 creates it. Log: `~/editor.log`.
- [x] Editor restarted with `./launch_editor.sh` (16 Oct). `nvidia-smi`: Kit uses 2.5 GB on GPU 0 and only 224 MB on GPU 1 (a basic driver context, down from 2.5 GB). That leaves about 45 GB free on GPU 1 for the NIM.

**Phase 0 complete.**

> **Note:** from now on the user runs all git commands; Claude doesn't run git in this project.

### Phase 0b: NIM setup (M, in parallel, background download)
Model: **Nemotron 3.5 Lightning 30B-A3B** (MoE, 3B active), image `nvcr.io/nim/nvidia/nemotron-3.5-lightning-30b-a3b:2.0.9-variant`, served name `nvidia/nemotron-3.5-lightning-30b-a3b`. **BF16 needs about 63 GB and won't fit on one 46 GB L40S, so use an FP8 tp1 profile.** NVFP4 is for Blackwell GPUs only.

- [x] 1. `NGC_API_KEY` in `~/.bashrc`; `docker login nvcr.io` done (by the user)
- [x] 2. Cache folder `~/.cache/nim` (`LOCAL_NIM_CACHE`)
- [x] 3. Profile chosen and saved as `NIM_MODEL_PROFILE`. It's a **mixed-precision** profile: FP8 attention plus 4-bit (W4A16 NVFP4) expert weights, with an FP8 KV cache. 21 GB on disk, and it runs on the L40S.
- [x] 4. `download-to-cache` done; the image is 33.8 GB
- [x] 5. Container `nemotron` running on GPU 1, port **8001**, `--restart unless-stopped`, tool calling and reasoning parser enabled. Ready about 130 s after start.
- [x] 6. `curl localhost:8001/v1/health/ready` returns `ready`
- [x] 7. `python nim_smoke_test.py`: **all pass** (16 Oct)
  - Fast chat 1.52 s (first call, includes warm-up)
  - Tool call 0.21 s: `get_zone_forecast({"zone": "walkway_station"})`, PASS
  - Tool result to briefing 0.46 s
  - GPU 1 shows 42 GB used; the NIM pre-allocates about 90% of memory for its KV cache, which is expected
- [x] Hosted endpoint works with the same `NGC_API_KEY`; checked via `python -m odtcc.agent --mode hosted` (16 Oct, about 70 s per card)

**Phase 0b complete.** Note for Phase 2: the generic briefing suggested "open additional entry points / delay entry", which is the wrong advice for an exit. The agent prompt must pass the real levers and the optimizer's numbers, and keep the model to those.

### Phase 1: Approval minute in the simulator, plus the optimizer (M, Step 5)
- [x] **Organized the files** (16 Oct; see `README.md` for the layout and commands):
  - `odtcc/` is the Python package (`generate_data`, `scenarios`, `predictor`, `build_twin`, plus `paths.py`, which holds every path in one place)
  - `kit/` holds code that runs inside Kit (`render_twin.py`; `exts/` goes here in Phase 4)
  - `scripts/` has `launch_editor.sh` and `nim_smoke_test.py`
  - `config/venue.json` · `docs/overview.md` · twins in `usd/` with their layers in `usd/layers/` (sublayer paths are now relative)
  - Commands are now `python -m odtcc.<module>` from the project root, and `scripts/launch_editor.sh` to start Kit
  - Re-verified: scores unchanged (99 → 0), twin rebuilt and layers resolve, predictor lead times unchanged, NIM test passes
- [x] `generate_data.simulate()`: added `venue_after` and `switch_minute`. Levers and routes switch at that minute, and the stadium release cap only applies from then on.
- [x] `scenarios.run(venue, plan, start_minute=0)`: backwards compatible. Verified that baseline and plan output are **identical** to the saved CSVs. Approval at minute 0/3/5/8/10 gives 0/0/0/38/41 dangerous minutes, matching the earlier scratch test.
- [x] `scenarios.py`: added `DEFAULTS` (lever values when nothing changes; the predictor uses them too) and `cost(plan)`:
  - `effort` runs from 0 (do nothing) to 5 (every lever at its limit)
  - Action-card figures: extra trains/hour, extra shuttle capacity, estimated buses (assuming 4 people/min per bus), holds crowd in bowl, redirects crowd
  - Demo plan: effort 2.76, +5 trains/hour, about 38 extra buses
- [x] `scenarios.py`: added `approval_deadline(venue, plan)`, the last approval minute that still gives 0 dangerous minutes. **Demo plan deadline is minute 5**; do nothing returns None. One simulation takes about 7 ms.
- [x] Regenerated `usd/twin_plan.usda` with the plan from minute 3 (`APPROVAL_MINUTE = 3` in `scenarios.py`; `scenario_scores.json` now also records the approval minute, plan cost and deadline)
  - **New headline:** 99 → 0 dangerous zone-minutes, peak 6.44 → **4.34** p/m², stadium clears at min **55** (instead of 28)
  - The twins match up to minute 2, then diverge. Walkway at min 18: 0.26 with the plan vs 5.06 with do nothing.
- [x] New `odtcc/optimizer.py` (`python -m odtcc.optimizer [--minute N]` writes `data/optimizer_result.json`):
  - [x] Grid of **2,250** lever combinations (6 release × 3 train × 5 shuttle × 5 north × 5 east), each simulated with approval at the alert minute
  - [x] Picks three **safe** options (0 dangerous zone-minutes), each with a different trade-off: `least_effort`, `most_margin` (lowest peak), `fastest_clear`
  - [x] Each pick gets its approval deadline and a robustness check (dangerous minutes across 5 noise seeds)
  - [x] Parallel with `ProcessPoolExecutor` on 32 cores: **1.1 s** for the sweep, about 2 s total
  - [x] Acceptance met: at minute 3, 1,028 of 2,250 plans are safe. The optimizer **beats `DEMO_PLAN`**:

    | Option | Peak | Cleared | Effort | Deadline | Robust | Levers |
    |---|---|---|---|---|---|---|
    | least_effort | 3.93 | 81 | 1.5 | 5 | 5/5 | release 800, trains every 3 |
    | most_margin | 1.37 | 81 | 2.25 | 12 | 5/5 | release 800, trains every 2, E→south 0.2 |
    | fastest_clear | 4.78 | **34** | 1.79 | 6 | 5/5 | release 2000, trains every 2, N→shuttle 0.5, E→south 0.2 |
    | demo plan (manual) | 4.34 | 55 | 2.76 | 5 | – | release 1200, trains 3, shuttles 300, N→shuttle 0.55, E→south 0.5 |
  - Notes for Phase 2:
    - The agent should present the options as trade-offs. Holding 70k people in the bowl until minute 81 is a real cost that `effort` underweights.
    - `fastest_clear` peaks at 4.78, close to the danger level of 5, so it has the thinnest margin.
- [ ] (stretch, **low priority now**) Vectorize `simulate()` with CuPy for a GPU sweep. The CPU sweep already takes about 1 s, so this only adds to the pitch ("NVIDIA GPU"), not to speed.
- [x] Retrained the predictor with **mid-run** plan starts:
  - Lever features now show DEFAULTS before the approval minute and the plan's values after it
  - Training plans start at minute 0 (30% of cases) or at a random minute from 1 to 30
  - `Forecaster.forecast(sim, plan, start_minute)`; inference runs on CPU, which removes the GPU/CPU mismatch warning
  - Held-out results: recall 99%, precision 83%, mean error 0.05 overall and 0.17 when busy. These are slightly worse than before because the test set now includes mid-event switches.
  - Do-nothing lead times: walkway 16 min, platform 16, north gates 18, shuttle 18
  - **After approval at minute 3: false alarms dropped from 18 (old model) to 6**, mean error 0.06. All 6 are on the station platform: predicted 4.6–5.0 vs actual 3.9–4.3, so they're cautious near-misses under the danger level of 5.
  - `python -m odtcc.predictor demo` now also prints this post-approval check

**Phase 1 complete** (only the optional CuPy GPU stretch remains).

### Phase 2: Nemotron agent (M, Step 6)
- [x] New `odtcc/agent.py`. Mode is set with `ODTCC_LLM`: `local` (NIM on :8001, the default), `hosted`, `cached` or `template`. Endpoint and model can be overridden with `ODTCC_LLM_BASE_URL` and `ODTCC_LLM_MODEL`.
- [x] Facts come from code (`Facts` class): forecast at the current minute, the optimizer's 3 options (cached in `optimizer_result.json`, or re-run for another minute), the do-nothing outcome, and a what-if `simulate()`
- [x] Nemotron gets 3 tools: `get_forecast`, `get_plan_options` and `simulate_plan`. It runs up to 4 tool rounds, then returns JSON: `recommended`, `briefing` (2–3 sentences about the chosen plan only) and `reason` (one sentence, no numbers).
- [x] **Division of labor** (learned from testing): Nemotron chooses and explains, and code writes every number and every comparison.
  - Early versions let the LLM write the comparisons and got them wrong: "4.7 is above 5", a made-up "8 min", "only 6 min longer" (really 47)
  - Now `compare()` builds the ✓ why-bullets and the trade-off lines against the other two options, so they're always correct
- [x] Action card (`build_card`): `alerts`, `recommended`, `plan` (the levers `/approve` will apply), `actions` (plain English), `expected`, `do_nothing`, `approve_by_minute`, `briefing`, `reason`, `why`, `trade_off`, all 3 `options`, and `requires_approval: true`
- [x] Guardrails:
  - [x] `simulate_plan` rejects levers outside `LIMITS`
  - [x] The recommendation must be `least_effort`, `most_margin` or `fastest_clear`; anything else triggers the template fallback
  - [x] Lever values and numbers on the card always come from the optimizer
  - [x] **Number check:** every number in the briefing or reason must appear in the tool results (rounding and % are allowed). Derived values (`minutes_left_to_approve`, `extra_minutes_to_clear_vs_do_nothing`) are supplied by the tools, so the model doesn't do arithmetic.
  - [x] Up to 2 **correction rounds**: the model is told exactly which numbers are wrong, or that its JSON is invalid. If it still fails, the template card is used.
  - [x] The agent can never approve (`requires_approval` is always true)
- [x] Fallbacks:
  - Any LLM error (connection, 30 s timeout, bad JSON, invalid option, made-up numbers) produces the template card, with `fallback_reason` set
  - The template picks the fastest-clearing option with a peak under 4.5
  - `cached` replays the last good LLM card for the same minute (`data/agent_cache.json`), otherwise it uses the template
  - Tested: template, NIM stopped (clean fallback), cached, and cached for a different minute (falls back to the template)
- [x] **Acceptance met:** `python -m odtcc.agent` with the local NIM
  - **20/20 valid Nemotron cards**, median **2.0 s** (max 2.8 s)
  - The pick is consistently `most_margin`: peak 1.37, approve by minute 12, clears at minute 81
  - The model sometimes calls `simulate_plan` on its own to test a custom idea before recommending
  - Before the fixes, only 3/5 to 9/10 runs produced a valid card
- [x] Hosted fallback works (`--mode hosted`, same pick) but took **70 s** on the free endpoint, so it's for emergencies only. For the demo, rely on the local NIM, then `cached`, then `template`.
- [x] Reasoning mode tested (`ODTCC_LLM_THINKING=1`): without a cap it ran past 4,000 tokens without answering. With `thinking_token_budget` it's about 4.6 s per step. **Left off by default.**

**Phase 2 complete.**
- For Phase 3: call `recommend()` in a worker thread so the server never blocks
- Pre-warm `cached` before the demo by running `python -m odtcc.agent` once
- Template and LLM picks differ: the template chooses `least_effort` by its rule, while Nemotron consistently chooses `most_margin`

### Phase 3: Brain service (M, the integration point)
- [x] New `odtcc/server.py` (FastAPI) on port 8000, run with `uvicorn odtcc.server:app --port 8000`
- [x] At startup: load forecaster, simulate baseline, build `twin_live.usda` + `crowd_live.usda` (seeded from baseline). Startup takes ~0.6 s.
- [x] `GET /state?minute=t`: per-zone density now, prediction for t+15, level, and alert band: `danger` (predicted ≥ 5), `caution` (4.5–5) or `ok`. Uses plan in force after approval.
- [x] `POST /recommend {minute}`: optimizer → agent → action card (blocking work runs in thread pool). Adds `before`/`after` aliases.
- [x] `POST /approve {plan, minute}`: validate levers, simulate from that minute, write `usd/layers/crowd_live.usda`, update server state, return new scores.
- [x] Refactored `build_twin.build_scenario(venue, scenario=None, df=None, crowd_file=None, twin_file=None)`. Added `build_crowd_layer(venue, df, crowd_file)` and `build_live_twin(venue, baseline_df)`. Added `CROWD_LIVE_FILE` / `TWIN_LIVE_FILE` constants.
- [x] `POST /reset`: rebuilds `crowd_live.usda` from baseline and clears approved state.
- [x] Mock JSON samples in `kit/exts/odtcc.command_center/mock/` (`state.json`, `recommend.json`, `approve.json`).

**Smoke-tested:** `/health` ready in 0.6 s · `/state?minute=3` fires caution on walkway + platform · `/approve` (most_margin at min 3) gives 99→0 dangerous zone-minutes, peak 1.37 · `/reset` restores baseline · `twin_live.usda` (391 B) and `crowd_live.usda` (301 KB) written to `usd/`.

**Phase 3 complete.**

### Phase 4: Kit extension (T, can start now with mocks; Steps 7 and 8)
- [x] Scaffold `kit/exts/odtcc.command_center/` (`config/extension.toml`, `odtcc/command_center/extension.py` + `client.py`)
- [x] Launch with `scripts/launch_editor.sh` (adds `--ext-folder kit/exts --enable odtcc.command_center`). **Start the server first**; it creates `twin_live.usda`.
- [x] On startup, open `usd/twin_live.usda`. The editor app **ignores a stage path on the command line**, so the extension opens it, set by `--/exts/odtcc.command_center/open_stage=...` (`launch_editor.sh [stage]` still works). It waits for RTX to render frames first; opening earlier **crashed Kit** (segfault in the Fabric scene delegate).
- [x] Panel (omni.ui), docked right of the viewport. Top to bottom: clock · alert banner · deadline countdown · Approve / Reset · before/after · Nemotron card (briefing, actions, why, trade-offs) · zone table
  - [x] Clock: minute = timeline time × time codes per second (4)
  - [x] Zone table colored by band, density now, predicted density
  - [x] Red banner for `danger`, amber for `caution`, green "Plan in force since minute N" after approval
  - [x] Approval deadline countdown (turns red once the deadline has passed)
  - [x] **Approve**, **Reset**, plus "Recommend now" (manual fallback) and "Open live twin"
- [x] Behavior:
  - [x] Timeline event stream → on each new minute `/state` via urllib in an executor thread (one request in flight; refetches the latest minute)
  - [x] First alert: auto-pause, then `/recommend`
  - [x] Approve: `POST /approve {plan, current minute}`, reload every used layer ending in `crowd_live.usda` (`Reload(force=True)`), resume playback
  - [x] Mock mode without the server: `ODTCC_MOCK=1` or `--/exts/odtcc.command_center/mock=true` (serves `mock/*.json`; the mock alert fires at minute 2)
- [x] **Tested in Kit (17 Oct):** Play → auto-pause at **minute 2** (walkway 4.52 caution) → `nemotron-local` card in **3.5 s**, most_margin, approve by minute 12 → Approve → `crowd_live.usda` rewritten and reloaded, playback resumes, banner "Plan in force since minute 2: no alerts", 99 → 0 dangerous zone-minutes, peak 6.44 → 1.18, clears min 83 vs 28. Clicking widgets needs a real mouse: synthetic XTEST clicks only reached the viewport toolbar, so Approve was exercised through code.
- [x] Lighting fix in `odtcc/build_twin.py`: sun 1000, dome 150; twins rebuilt. Still to tune by eye. `build_twin` CLI no longer deletes the server-owned live files.
- Notes:
  - The demo alert fires at **minute 2** (the original story said approve at 3); approve at whatever minute it pauses. The optimizer cache is for minute 3, so the minute-2 sweep runs live (~2 s, already inside the 3.5 s).
  - The panel calls `/reset` when it starts (Kit launch or hot reload). The server keeps its approved plan across Kit restarts, and with a plan in force there are no alerts, so playback never paused (found in testing 17 Oct). Press **Reset** between rehearsals.

**Phase 4 core complete.** Still to check by hand:
- [ ] Click **Approve** with a real mouse (only exercised through code so far)
- [ ] Watch the viewport after Approve: walkway/platform pillars should stay low around minutes 15–25 instead of turning red
- [ ] Tune the lighting by eye; maximize the Kit window for the demo
- [ ] Mock mode run (`ODTCC_MOCK=1`, server stopped)
- [ ] Optional: slow playback around minutes 0–20 so the audience can follow
- [ ] (stretch) Ghost pillars: a translucent layer showing the density predicted 15 minutes ahead

### Phase 5: Demo hardening and pitch (both, Step 9). **Superseded by section 0 (18 Oct)**; kept for reference
- [ ] Recreate `scripts/render_all.sh`: simulate, build twins, headless render, ffmpeg to `renders/twin_<s>.mp4`
- [ ] Render the baseline and plan MP4s as the backup
- [ ] `demo_up.sh`: start the NIM (if it's not running), then the server, then Kit with the extension (the server must come first: it creates `twin_live.usda`); health-check all three
- [ ] Pre-warm the offline fallback for the real alert minute: `python -m odtcc.agent --minute 2` (the cached card only replays for the same minute)
- [ ] Screen-record one full successful run as the final backup
- [ ] Rehearse the 3-minute flow at least 3 times, including the fallback (hosted endpoint or cached card)
- [ ] Pitch slides:
  - [ ] Problem
  - [ ] Live demo
  - [ ] Architecture, naming the NVIDIA stack (Omniverse Kit, OpenUSD, RTX, cuDF, XGBoost on CUDA, Nemotron NIM)
  - [ ] Approval-deadline insight
  - [ ] Honest caveats: synthetic venue; predictor trained on the simulator; the recommended plan delays stadium clearing (min 83 vs 28)
  - [ ] Ops-as-a-Service: LA city infrastructure after the Games, Brisbane 2032
- [ ] **Feature freeze after Day 2 lunch**

---

## 6. Schedule

| When | M (brain) | T (face) |
|---|---|---|
| Now | Phase 0, start the NIM download | Scaffold the extension, lighting fix |
| Day 1 PM | Phase 1 (approval minute, optimizer), Phase 2 (agent) | Panel and timeline sync with mocks, auto-pause |
| Day 1 evening | Phase 3 (server); **full loop working end to end** | Approve → live layer reload against the real server |
| Day 2 AM | Predictor retrain with mid-run plans, GPU sweep (stretch) | Polish, ghost pillars (stretch), MP4 backups |
| Day 2 PM | Rehearse and record backup | Pitch deck and rehearsal |

---

## 7. Demo script (3 minutes)

1. Kit shows the Coliseum twin, all green. Press Play: the final whistle at minute 0.
2. At minute 2 the timeline auto-pauses. Amber banner: *"Walkway to Expo Park/USC station approaching danger (4.52 p/m²) by minute 17."*
3. About 3.5 s later the Nemotron card appears: the most_margin plan (hold the crowd at 800/min, a train every 2 min, 20% of east-gate exits sent south), why it works, and *"approve by minute 12"*.
4. The operator clicks **Approve** at minute 2. Playback resumes, the banner turns green (*"Plan in force since minute 2: no alerts"*), and the walkway and platform stay low.
5. Before/after: **99 → 0 dangerous zone-minutes**, peak 6.44 → 1.18 p/m². The trade-off is that the stadium clears at minute 83 instead of 28.
6. Close: permanent LA infrastructure, then Ops-as-a-Service for future host cities.

---

## 8. Risks and fallbacks

| Risk | Fallback |
|---|---|
| NIM download or startup fails | Hosted build.nvidia.com endpoint (change `base_url`); then the cached card |
| LLM returns bad JSON or is slow | Templated action card from the optimizer's numbers |
| Kit crashes during the demo | Pre-rendered MP4s and the screen recording |
| Layer reload doesn't refresh the viewport | Re-open `usd/twin_live.usda` after approval (slower, but it works) |
| Optimizer too slow | Coarser grid, or a cached `optimizer_result.json` for minute 2 (`python -m odtcc.optimizer --minute 2`) |
| Kit started without the twin | Click **Open live twin** in the panel (the server must be running so `twin_live.usda` exists) |
| Predictor misses after approval | Keep the baseline forecast for the alert; after approval, show simulated densities only |
