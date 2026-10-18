# ODT-CC — Hackathon Chat Export

> **Historical (15 Oct 2026).** Notes from the start of the hackathon. Much of the "pending" work below has since
> been built; for the current system see [README.md](../README.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

*Olympic Digital Twin Command Center · NVIDIA track · Presidio office hackathon*
*Exported 15 Oct 2026 · Team of 2 · 2-day hackathon · Omniverse beginners*

---

## Contents
1. [NVIDIA pre-hackathon session notes](#1-nvidia-pre-hackathon-session-notes)
2. [The idea and use case](#2-the-idea-and-use-case)
3. [Implementation plan](#3-implementation-plan)
4. [What's built so far](#4-whats-built-so-far)
5. [Launchpad machine and environment](#5-launchpad-machine-and-environment)
6. [Omniverse: what works and what doesn't](#6-omniverse-what-works-and-what-doesnt)
7. [Command cheat sheet](#7-command-cheat-sheet)
8. [Problems hit and fixes](#8-problems-hit-and-fixes)
9. [Decisions and Q&A](#9-decisions-and-qa)
10. [Next steps](#10-next-steps)

---

## 1. NVIDIA pre-hackathon session notes

### Day 1 — Steve Rick (Solutions Architect, NVIDIA partner team)
*Transcript started ~20 min in; slides between 22:26 and 39:40 not captured.*

- **Why partners:** NVIDIA can't reach every customer; partners like Presidio take the stack to market.
- **RAG matters more than ever for agents:** embed → retrieve → re-rank → answer, so agents use data beyond their frozen weights.
- **Think in the full lifecycle:** get/generate data → prepare → (optionally) fine-tune and evaluate → deploy, retrieve, guardrail, monitor, iterate.
- **Blueprints are reference architectures** — "computational Lego bricks": swap models, drop or add components.
- **NeMo building blocks:** Curator, Customizer, Evaluator (data/fine-tuning/eval); Guardrails; Retriever (RAG); Agent toolkit.
- **build.nvidia.com** — the starting point for models (NIMs), blueprints, skills, playbooks.
  - "Free endpoint" models = rate-limited APIs, fine for demos, not production. Self-host where possible.
  - Nemotron 3 Ultra (550B MoE, ~55B active) is too big for hackathon hardware; the ~30B "Lightning" (~3B active) is realistic.
  - Deploying a NIM: `docker run` with all GPUs, cache env vars, exposed port, then call it as an API.
  - Blueprint highlight: **NemoClaw for Hermes** — semi-autonomous agent inside the **OpenShell** sandbox (deny-all by default).
- **Launchpad:** remote Ubuntu GPU box (H100s / L40s), access via browser desktop, web terminal, SSH, code-server, JupyterLab. Omniverse projects get render-capable cards.
- **His challenge:** APIs are allowed, but he'd be "especially impressed" by teams self-hosting models on the provided GPUs.
- **Action items:** account on build.nvidia.com with green check for Presidio's NGC org; generate an NGC API key (`docker login nvcr.io`, user `$oauthtoken`); watch for the Launchpad access email.

### Day 1 — Dave Barry (from 1:08:54)
- **Retrieval models:** embedding, re-ranking, extraction (**Nemotron Parse** / NeMo Retriever).
- **Ingestion/chunking is maybe the most important RAG step** — PDFs, tables, graphs, watermarks, multi-page procedures are hard.
- **Enterprise RAG blueprint** = most popular, "foundational" blueprint; many variants.
- **Context engineering → harness engineering:** memory (context window) is the bottleneck, so an orchestrator spawns sub-agents with their own context windows (like parallel processing; scales horizontally). Harness metaphor = horses harnessed to one carriage.
- **Reasoning models** loop over their own work (like reviewing test answers).
- **NVIDIA Agent Toolkit** (umbrella), works with any harness. **cuVS** (GPU vector search), **cuDF** (GPU dataframes), **cuOpt** (optimization). **Skills** = reusable markdown instructions.
- **NeMo Agent Toolkit** = open-source profiling/observability for agent harnesses.
- **AI-Q blueprint** (LangChain): intent router → small model for shallow queries / orchestrator + sub-agents for complex ones; OpenShell sandbox around it.
- **Nemotron family (all MoE, open-weight):** 3.5 Lightning (newest, strong tool calling — "you'll almost certainly use this"), Super, Ultra (near-frontier). Plus VLMs, Parse, Nano Omni (multimodal), content-safety models, voice models (~95% of ElevenLabs quality, cheaper; multilingual, custom voice, full duplex).

### Day 2 — Jeff Spaulding (physical AI / Omniverse)
*Gap in transcript 9:46–13:44.*
- **Physical AI:** ~10M factories, 200K warehouses to automate; models must understand physics. Data is king (GR00T, Alpamayo ~80K hours of driving data).
- **OpenUSD:** Pixar 2011 → Apple USDZ 2018 → NVIDIA all-in 2019 → Alliance for OpenUSD. A scene is a **stage** made of **layers** (geometry, physics, sensors…). **SimReady** = physically accurate assets. **USDA** (text format) is agent-friendly.
- **Omniverse libraries:** **OV RTX** (ray-traced rendering/sensors), **OV PhysX** (USD-native physics), **OV Stage**; plus streaming, UI, packaging, CAD→USD, thousands of USD assets, real-time viewer skill.
- **Blueprints:** Real-Time Digital Twin (fluid sim), physical AI factory, **Omniverse DSX** (gigawatt AI-factory twins; live demo on build.nvidia.com).
- **His demo:** an agent built a USD Composer clone streaming from a remote L40 to his MacBook browser.
- **His challenge:** learn OpenUSD fundamentals; use agents + Omniverse libraries to build simulation pipelines. He wants Presidio-relevant use cases.

---

## 2. The idea and use case

> **ODT-CC** is a real-time Omniverse digital twin of LA28 venue zones that uses GPU-accelerated AI to predict crowd, traffic and transit bottlenecks before they happen. A unified command dashboard recommends actions for human approval. It becomes permanent city infrastructure after the Games and is licensable to future host cities as Ops-as-a-Service.

**Hackathon scope:** one venue zone, one simulated event day, end to end.

**Demo scenario:** Final whistle at the **LA Memorial Coliseum**; 70,000 people leave. Most head for the Expo Park/USC station, but trains take only 1,000 people every 4 minutes. The walkway and platform become dangerous (>5 people/m²) ~18 minutes in, while the south side and parking stay nearly empty.

**The 3-minute demo story:**
1. The twin shows the Coliseum zone; the match ends.
2. At minute ~2–3, ODT-CC predicts the station walkway will be dangerous at ~minute 18.
3. The Nemotron agent recommends a plan (stagger release, more trains, shuttles, reroute south) and explains why.
4. The operator clicks **Approve** (human in the loop).
5. The twin switches to the plan scenario — red zones stay green/yellow; dangerous minutes drop from 99 to 0.
6. Pitch close: permanent LA infrastructure → licensed to Brisbane 2032 etc.

---

## 3. Implementation plan

### Architecture
| Layer | Does | Tech | Runs on |
|---|---|---|---|
| Venue model | Gates, plazas, walkway, station layout | `venue.json` (single source of truth) | Any |
| Simulator | Minute-by-minute crowd flow with queue back-pressure | Python + cuDF (`cudf.pandas`) | GPU |
| Twin | Animated 3D density view | OpenUSD layers + Omniverse (USD Viewer streaming) | GPU 0 |
| Predictor | Each zone's density 15 min ahead | XGBoost on GPU | GPU |
| Plan optimizer | Best intervention mix within limits | GPU simulation sweep; cuOpt stretch | GPU |
| Agent | Operator briefing + action card | Nemotron 3.5 Lightning NIM (self-hosted) | GPU 1 |
| Dashboard | Alerts, twin, Approve, before/after | FastAPI + web page + embedded stream | Box → browser |

**Team split:** Mohamed = simulator, predictor, optimizer, agent ("brain"). Teammate = twin, dashboard, pitch ("face").

### Steps
| # | Step | Status |
|---|---|---|
| 0 | Environment (Launchpad, venv, files, cuDF) | ✅ Done |
| 1 | Data + simulation | ✅ Done |
| 2 | OpenUSD twin | ✅ Done (viewed in Kit Base Editor; streaming works) |
| 3 | Scenario engine + layered USD | ✅ Done |
| 4 | Predictor (15-min forecaster) | ✅ Built — run on Launchpad |
| 5 | Plan optimizer | ⏭ Next |
| 6 | Nemotron agent (self-hosted NIM on GPU 1) | Pending |
| 7 | Dashboard with Approve | Pending |
| 8 | Twin embedded in dashboard (stream) | Streaming proven; integration pending |
| 9 | Demo polish + pitch | Pending |

**Timeline:** before kickoff → Steps 0–4 + one NIM test. Day 1 → Steps 5–7 (full loop working). Day 2 → Step 8, polish, backup recording, rehearse. No new features after Day 2 lunch.

### Risks and fallbacks
| Risk | Fallback |
|---|---|
| Omniverse window won't open on remote desktop | ✅ Hit it — solved with headless render + streaming |
| Streaming into dashboard fails | RTX-rendered MP4s (`render_all.sh`) swapped on Approve |
| NIM won't start | Hosted build.nvidia.com endpoint (one-line `base_url` change) |
| cuOpt too slow to set up | Simulation sweep gives a defensible optimized plan |
| Predictor weak | Narrow to the station walkway, which carries the story |

---

## 4. What's built so far

All in `~/Desktop/Olympic_DigitalTwin/Nvidia/` on Launchpad (git repo).

| File | Purpose |
|---|---|
| `venue.json` | 11 nodes (stadium, 4 gates, 2 plazas, walkway, platform, shuttle stop, parking) with positions, areas, capacities, routing shares. Synthetic/schematic. |
| `generate_data.py` | Simulates 120 minutes of egress. Max density 6 p/m²; full zones block upstream flow. Writes `data/egress_baseline.csv`. |
| `scenarios.py` | Operator levers + realistic ranges; runs "do nothing" vs demo plan; writes `data/egress_<name>.csv` + `data/scenario_scores.json`. |
| `build_twin.py` | Builds layered OpenUSD: `usd/venue_layout.usda` (static), `usd/crowd_<s>.usda` (animated overrides), `twin_<s>.usda` (open these). |
| `render_twin.py` | Runs inside a Kit app, headless; renders one RTX PNG per simulated minute. |
| `render_all.sh` | One command: simulate → build twins → headless RTX render → MP4s. |
| `predictor.py` | 15-minute-ahead XGBoost forecaster; `train` and `demo` commands. |

### Operator levers (`scenarios.py`)
| Lever | Default | Range |
|---|---|---|
| Stadium release rate (people/min) | no limit | 800–2,500 |
| Minutes between trains | 4 | 2–4 |
| Shuttle capacity (people/min) | 150 | 150–450 |
| North crowd steered to shuttles | 40% | 40–80% |
| East-gate exits redirected south | 0% | 0–80% |

**Demo plan:** release 1,200/min · trains every 3 min · shuttles 300/min · 55% to shuttles · 50% east→south.

### Scenario results
| Metric | Do nothing | With plan |
|---|---|---|
| Dangerous zone-minutes | 99 | **0** |
| Zones ever dangerous | 4 | **0** |
| Peak density (p/m²) | 6.44 | **3.17** |
| Stadium cleared by | minute 28 | minute 58 |

Honest trade-off: the plan holds people in the bowl longer (+30 min to clear) but nobody is crushed.

### Predictor results (held-out scenarios)
- Trained on **600 simulated exits** (45k–80k crowd, random plans/noise), 504K training rows.
- Dangers caught (recall): **100%** · Alerts that were real (precision): **83%** · Mean error in busy zones: **0.11 p/m²**

| Zone | Alert fires | Danger starts | Warning |
|---|---|---|---|
| Station walkway | min 2 | min 18 | **16 min** |
| Station platform | min 3 | min 19 | **16 min** |
| North gates | min 6 | min 26 | **20 min** |
| Shuttle loading | min 19 | min 37 | **18 min** |

**Caveat for judges:** trained on our own simulator, so numbers are optimistic. In production it trains on live sensor feeds (turnstiles, camera counts, Metro ridership).

### What to observe in the twin
`twin_baseline.usda` → press Play (Window → Animation → Timeline; 2 hours plays in 30 s):
- Min 0–15: all green · ~18: walkway red, pillar rises · ~19–20: platform red · ~26: north gates red · ~37: shuttle loading red · 80+: calming · south side/parking stay green.

`twin_plan.usda` → walkway and station stay green/yellow throughout.

Known cosmetic issue: scene too bright in RTX (colors washed out) — tone down sun/dome intensity in `build_twin.py`.

---

## 5. Launchpad machine and environment

- Host: `presidio-hackathon-03-gpu01`, user `nvidia`, Ubuntu, Python 3.12.3, Docker 29.4.3
- **GPUs: 2× NVIDIA L40S, 46 GB each** (render-capable), driver 595.71, CUDA 13.2
- Disk: 1.7 TB free
- Access: Coder IDE (preferred; drag-drop uploads <100 MB), noVNC desktop, WebSSH, Grafana metrics
- Claude Code and Codex CLI are preinstalled on the box
- Project: `~/Desktop/Olympic_DigitalTwin/Nvidia/` · venv at `.venv/`
- Kit app template: `~/Desktop/kit-app-template/` (built apps: `my_company.my_usd_composer`, `my_company.my_usd_viewer`, `my_company.my_editor`, each with `_streaming` variants)
- Web viewer: `~/Desktop/web-viewer-sample/`

---

## 6. Omniverse: what works and what doesn't

| Approach | Result |
|---|---|
| `./repo.sh launch` from desktop terminal | ❌ `GLXBadFBConfig` — noVNC desktop has no GPU display |
| VirtualGL (`vglrun`) | ❌ Not installed |
| Launch from SSH | Starts ("app ready") but headless — nothing to see; "no default window" errors are harmless |
| **Headless render** (`env -u DISPLAY … --no-window --exec render_twin.py`) | ✅ RTX PNGs → MP4 |
| **USD Viewer streaming + NVIDIA web-viewer-sample in Chrome on the Launchpad desktop** | ✅ Live interactive RTX viewport in browser |
| Kit Base Editor | ✅ Opens and renders `twin_*.usda` |

**Why Chrome:** Omniverse streaming uses WebRTC; NVIDIA's client supports Chrome/Edge only. Firefox stalls at "Waiting for stream to begin".

**Why USD Viewer for the demo:** built for browser streaming and accepts commands from the web page (the "USD Asset" dropdown loads files) — so the Approve button can switch baseline → plan. Kit Base Editor works too but would need custom messaging code. USD Composer isn't needed (no hand-editing); optional for visual polish via its streaming build.

**Isaac Sim:** not used — it's for robotics. Our "agents" are crowds/vehicles in a city network, so a GPU flow simulator + OpenUSD rendering fits.

---

## 7. Command cheat sheet

```bash
# Every new terminal
cd ~/Desktop/Olympic_DigitalTwin/Nvidia && source .venv/bin/activate

# Data, scenarios, twins
python -m cudf.pandas generate_data.py
python -m cudf.pandas scenarios.py
python build_twin.py

# Predictor
pip install xgboost
python predictor.py train      # should say (cuda) on Launchpad
python predictor.py demo

# Full RTX video pipeline (headless)
./render_all.sh 5              # preview, frame every 5 min
./render_all.sh                # full quality → renders/twin_<s>.mp4

# Streaming viewer (run in background so SSH drops don't kill it)
pkill -f my_usd_viewer_streaming; pkill -f vite
cd ~/Desktop/kit-app-template
nohup env -u DISPLAY ./_build/linux-x86_64/release/my_company.my_usd_viewer_streaming.kit.sh --no-window > ~/viewer.log 2>&1 &
cd ~/Desktop/web-viewer-sample
nohup npm run dev > ~/webviewer.log 2>&1 &
ss -tlnp | grep -E "49100|517"           # expect 49100 (Omniverse) and 5173 (viewer)
# Then open http://localhost:5173 in CHROME on the Launchpad desktop → "UI for default streaming USD Viewer app"
grep -v thread_init ~/viewer.log | tail -20   # server log without noise

# GPU usage
nvidia-smi
watch -n 2 nvidia-smi
nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total --format=csv -l 2

# Git
git add -A && git commit -m "message" && git push
```

---

## 8. Problems hit and fixes

| Problem | Fix |
|---|---|
| `python: command not found` | Use `python3`, or activate the venv |
| venv failed (`ensurepip` missing) | `sudo apt install -y python3.12-venv`, `rm -rf .venv`, recreate |
| Simulator queue/negative occupancy bug (dev) | Clamp outflow at ≥0; capacity-limited inflow with back-pressure |
| USD layers read zero density | All layers must share `timeCodesPerSecond`/`framesPerSecond` |
| `.usda` opened in text editor | It's text; open it from inside an Omniverse app (File → Open) |
| `launch: command not found` | It's `./repo.sh launch`, run inside `kit-app-template` |
| `GLXBadFBConfig` | Desktop has no GPU display; use headless (`env -u DISPLAY`, `--no-window`) or streaming |
| Pasted shell text ended up inside `render_twin.py` | `sed -i 's/^asyncio\.ensure_future(render()).*/asyncio.ensure_future(render())/'`; download files via browser/Coder instead of pasting |
| Can't type in terminal | Click into it; Ctrl+Q (unfreeze after Ctrl+S); Ctrl+C; or new terminal / Coder |
| Stream stuck at "Waiting for stream to begin" | Use Chrome, not Firefox; one viewer tab only |
| `main: thread_init: already added for thread` spam | Harmless log noise; filter with `grep -v thread_init` |
| SSH dropped and killed servers | Start servers with `nohup … &` |
| App name is `my_company.*` not `odtcc.*` | Defaults were accepted; cosmetic, leave it |

---

## 9. Decisions and Q&A

- **Venue:** LA Memorial Coliseum / Exposition Park (rail station next to venue = clean crowd + transit story).
- **Track:** NVIDIA only. **Team:** 2 people, 2 days, Omniverse beginners → schematic 3D, not photoreal.
- **Hosted APIs vs containers:** build.nvidia.com endpoints for development; self-hosted NIM container on Launchpad for the demo (same OpenAI-style API; change `base_url` only). Omniverse, cuDF, cuOpt run as software on the box, not APIs.
- **Blender:** was only a stopgap viewer; dropped once Launchpad was available.
- **OpenUSD:** yes — the whole twin is OpenUSD (`pxr` Python API, `.usda` text format, layered stage, time samples, custom `odtcc:density` attributes).
- **Resource risk of headless render:** one GPU for a minute or two, tens to hundreds of MB disk, no API quota.
- **Usage/limits:** Grafana Metrics link (readme), `nvidia-smi`; Launchpad reservation is time-based; build.nvidia.com limits show as HTTP 429.

---

## 10. Next steps

1. **Check the twin** in Kit Base Editor: play baseline and plan; report camera/brightness issues (lighting likely needs toning down).
2. **Run the predictor on Launchpad:** `pip install xgboost && python predictor.py train && python predictor.py demo`; confirm `(cuda)`; commit `predictor.py` and `models/`.
3. **Load the twins into the streaming viewer:** `grep -rn "Sample 2" ~/Desktop/web-viewer-sample/src` → add *ODT-CC: Do nothing* and *ODT-CC: With plan* entries pointing at the `twin_*.usda` paths.
4. **Step 5 — Plan optimizer:** GPU search over lever combinations when an alert fires; return the best plan + numbers.
5. **Step 6 — Nemotron agent:** self-host Nemotron 3.5 Lightning NIM on GPU 1; tools call predictor + optimizer; produce briefing + action card.
6. **Step 7 — Dashboard:** alerts, embedded twin stream, Approve → switch to plan twin, before/after metrics.
7. **Ask NVIDIA organizers** (optional now that local streaming works): whether streaming to laptops directly is possible on Launchpad.
8. **Pitch prep:** 3-minute flow, backup MP4 recording, Ops-as-a-Service slide.