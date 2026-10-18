# ODT-CC Demo Video Script (under 5 minutes)

Target length **4:35**. Shows the end-to-end flow: planning mode → live operations → an unscripted incident →
re-plan → audit trail. Numbers below are what the system produces (checked 18 Oct); if a live run differs
slightly, say what is on screen.

## Context for judges (say or show before the walkthrough)

1. **What the twin is for.** A shared live picture of a venue zone for LA28, LA Metro, LADOT and police. It
   forecasts crowd danger 15 minutes ahead from sensor data, tests 2,250 plans in about a second, has Nemotron
   explain three scored options, and applies the plan a human approves to an OpenUSD twin in Omniverse. It
   works before the event (planning mode) and on the day (operations mode).
2. **Our scenario.** 70,000 people leave the LA Memorial Coliseum at the final whistle; most head for the
   Expo Park/USC station walkway (600 people/min), which reaches crush density (5 people/m²) at minute 18 if
   nobody acts. The venue layout is schematic and all data is synthetic: a crowd simulator gives the truth,
   synthetic sensors (ticket scans, Bluetooth counts from ~30% of phones, gate counters, train departures) give
   what a command centre would actually see, and a world agent causes unscripted incidents.
3. **Dashboard features.** Scenario picker; recommendation card with three scored options and Approve buttons;
   zone table with forecast and confidence; View as an agency; incident feed with Inject incident; Your plan
   (five levers, Simulate, Approve my plan); planned-event inputs and Compare plans; decision log.
4. **What approving does.** The plan is applied from that minute, the evening is re-simulated, the twin's live
   crowd layer is rewritten and Kit reloads it, so the 3D view follows the approved plan; the decision is logged.

## Before recording (10 minutes)

1. `scripts/demo_up.sh`, then `scripts/warm_caches.sh` (saved cards in case the NIM is slow).
2. Kit maximized on the left two thirds of the screen; dashboard (`http://localhost:8000/dashboard/`) on the
   right third. Browser zoom 80–90% so the Operations card and the zone table fit.
3. In the dashboard: **Scenario → Normal egress**, **Reset**. Badge reads "Following Kit (3D twin)".
4. Have `docs/diagrams/01_system_overview.svg` open in a browser tab for the architecture shot.
5. Do one full rehearsal run, then **Reset** before recording.
6. Record the screen from your laptop (the Launchpad desktop in the browser) or with any screen recorder;
   1080p, system audio off, microphone on.

## Script

| Time | Screen | Do | Say |
|---|---|---|---|
| **0:00–0:20** | Kit: Coliseum twin, all green | – | "When the final whistle blows at the LA Memorial Coliseum, 70,000 people leave at once. Most head for one rail station, Expo Park/USC. Within about 18 minutes the walkway and the platform reach crush density. By the time someone radios it in, it's too late." |
| **0:20–0:45** | Architecture diagram (`01_system_overview.svg`) | – | "ODT-CC is a digital twin command center. An OpenUSD twin rendered in NVIDIA Omniverse, synthetic live feeds, a GPU-trained XGBoost forecaster that looks 15 minutes ahead, an optimizer that tests 2,250 plans in about a second, and a self-hosted Nemotron NIM that explains the options. A human always approves." |
| **0:45–1:05** | Dashboard, Planning column, minute 0 | Your plan: set **Train every = 2** → **Simulate** | "Planning mode, before the event. A planner's first idea: double the trains. The twin says UNSAFE: 22 dangerous zone-minutes, the walkway still hits 6 people per square metre." |
| **1:05–1:40** | Planning column | **Save as Plan A** → **Recommend now** → **Add AI plan** → set planned event: 2nd wave **0.30** after **15** min, nearby event **12,000** at min **15**, trains every **5** × **900**, tick **Vermont Ave** → **Compare** | "Now the event as planned: a staggered second wave, an event letting out next door, a thinner timetable, Vermont Avenue closed. Do nothing: 155 dangerous zone-minutes. Plan A: still unsafe. The AI's plan: safe, with 56 extra buses and the stadium clearing later. A bad plan found here costs nothing." |
| **1:40–1:50** | Dashboard header | **Scenario → Live: random incidents** | "Now event day. In live mode, incidents are not scripted. A world agent can cause them at any moment." |
| **1:50–2:15** | Kit + dashboard | **Play**. Point at the sensor strip and the Kit view | "The command center never sees the true crowd: it sees ticket scans and Bluetooth counts from about a third of phones. The forecaster turns those estimates into a 15-minute forecast with a confidence for every zone." |
| **2:15–2:50** | Auto-pause at minute 3, card appears | Point at the banner, the card, the 3 options | "Minute 3: walkway approaching danger by minute 18, fifteen minutes of warning. The optimizer found three safe plans; Nemotron recommends most margin: hold the crowd in the bowl, a train every two minutes, more shuttles, steer the north plaza to them. Every action is tagged with the agency that does it, and each option has a score." |
| **2:50–3:05** | View as | **View as → LA Metro**, then back to **Everyone** | "Each agency sees its own tasks." |
| **3:05–3:20** | Before/after, Kit | **Approve AI plan** | "The operator approves. 99 dangerous zone-minutes become zero. The cost is honest: the stadium clears at minute 81 instead of 28. In the twin, the walkway stays green and the blue disc shows people held safely in the bowl." |
| **3:20–3:50** | Incident feed | After a few minutes of play: **Pause** → pick **rail signal failure** → **Inject incident now** → **Play** | "Now something nobody scripted: an E Line signal failure. The LA Metro alert pauses the run. The twin can't predict that an incident starts, but it sees it on one shared screen and re-plans from this minute, on top of the plan already in force." |
| **3:50–4:10** | Re-plan card, Kit beacon | Point at "keeping the current plan → N dangerous minutes" → **Approve this option** on the recommended option | "Keeping the current plan would now be dangerous. The re-plan fixes it, and the red beacon marks the platform with no trains." |
| **4:10–4:25** | Decision log | Scroll to the Decision log | "Every alert, recommendation, score and approval is in the decision log, so the after-action review knows who decided what, and when. Unreported crowds are caught from the sensors too." |
| **4:25–4:35** | Kit full screen or title card | – | "The data the twin needs already exists: ticketing, Metro feeds, city traffic data. ODT-CC joins it into one picture, and stays as LA city infrastructure, licensable to Brisbane 2032 and beyond." |

## If something goes wrong while recording

| Problem | Do |
|---|---|
| The card takes long or the source says `template` | Keep talking; the template card has the same numbers. Or say "the system falls back to a saved card if the model is down". |
| A random incident arrives before you inject one | Use it: it pauses, show its re-plan, skip the inject step |
| The re-plan says "No change needed" | Say "the plan in force already copes; no change needed", then inject **nearby event** for a second try |
| Kit misbehaves | **Reset** in the dashboard; worst case record the dashboard alone in standalone mode (`?standalone=1`) |

## Key numbers (for captions or slides)

* 15 minutes of warning; 97% of dangers caught by the forecaster
* 2,250 plans tested in about 1.2 s; Nemotron card in about 2 s
* 99 → 0 dangerous zone-minutes (normal egress); 129 → 0 with the Expo Line delay
* Trade-off: stadium clears at minute 81 instead of 28
