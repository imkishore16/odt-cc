"""ODT-CC Command Center panel.

Follows the timeline (minute = timeline time x time codes per second), asks the brain
service for the zone state each new minute, auto-pauses on the first alert and fetches
the Nemotron action card. Approve sends the plan to the server, which rewrites
usd/layers/crowd_live.usda; the panel reloads that layer and resumes playback.
"""
import asyncio
import json
import os
from pathlib import Path

import carb
import carb.settings
import omni.ext
import omni.kit.app
import omni.timeline
import omni.ui as ui
import omni.usd
from omni.kit.viewport.utility import get_active_viewport, next_viewport_frame_async

from .client import BrainClient

PROJECT_ROOT = Path(__file__).resolve().parents[5]
TWIN_LIVE = PROJECT_ROOT / "usd" / "twin_live.usda"
CROWD_LIVE_NAME = "crowd_live.usda"
SETTINGS = "/exts/odtcc.command_center"

# omni.ui colours are 0xAABBGGRR
RED, AMBER, GREEN = 0xFF2828D0, 0xFF1E9CF0, 0xFF48A848
GREY, WHITE, PANEL = 0xFF9A9A9A, 0xFFEEEEEE, 0xFF2A2A2A
BAND_COLOUR = {"danger": RED, "caution": AMBER, "ok": GREEN}
VIEWS = ["Everyone", "LA Metro", "LA28 Venue Ops", "LA28 Transport", "LADOT", "LAPD"]   # role-based views


def for_view(actions, view):
    """The actions a given agency carries out (each action starts with "[Owner / Owner]")."""
    if view == "Everyone":
        return list(actions)
    return [a for a in actions if a.startswith("[") and view in a[1:a.index("]")]]


INCIDENT_TYPES = [("capacity", "Capacity drop"), ("inflow", "Extra crowd arrives"), ("sensor_outage", "Sensor outage")]


def _fmt_min(m):
    return "-" if m is None else f"min {m}"


class CommandCenterExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._tasks = set()
        settings = carb.settings.get_settings()
        # The server rewrites crowd_live.usda on approve/reset; reload it silently instead of a "fetch changes" prompt
        settings.set("/persistent/ext/omni.kit.usd.layers/auto_reload_sublayers", True)
        mock = bool(settings.get(f"{SETTINGS}/mock")) or os.environ.get("ODTCC_MOCK") == "1"
        url = settings.get(f"{SETTINGS}/server_url") or "http://localhost:8000"
        self._client = BrainClient(url, mock=mock)

        self._minute = None
        self._state = None         # last /state response
        self._card = None          # /recommend action card
        self._approved = None      # last /approve response
        self._card_used = False    # the current card has been approved or dismissed
        self._seen_reports = set() # incident reports already handled: (id, minute, status)
        self._decided_alerts = set()   # zones alerting when the operator last decided
        self._last_decision = None     # minute of the last pause/decision
        self._inject_kind = 0      # index into ["random"] + incident kinds
        self._view = 0             # index into VIEWS (role-based view)
        self._last_fingerprint = None   # last /state drawn
        self._cmd_task = None      # the dashboard's commands run one after another
        self._gen = 0              # +1 per reset: answers to requests sent before it are discarded
        self._resetting_to_zero = False   # after a reset, wait until the timeline really is at minute 0
        self._recommend_again = None      # a newer incident arrived while a recommendation was in flight
        self._busy = None          # "recommending" | "approving" | "resetting"
        self._status = "mock data (no server)" if mock else f"connecting to {url}"
        self._state_task = None
        self._state_dirty = False
        self._levers = None        # slider definitions from /levers
        self._scenarios = None     # {"current": id, "scenarios": [...], "zones": [...]} from /scenarios
        self._inc = {"type": "capacity", "node": "station_platform", "from": 10, "duration": 15,
                     "remaining_pct": 0, "per_min": 400}   # custom incident being built
        self._inc_open = False     # builder section expanded
        self._my_plan = {}         # operator's own lever values
        self._my_result = None     # /simulate response for _my_plan
        self._my_stale = False     # sliders moved since the last Simulate
        self._saved_plans = {}     # planning mode: name -> levers, compared side by side
        self._planning = {"attendance": 70000, "stagger_share": 0.0, "stagger_delay": 0,   # planning inputs
                          "nearby_event_people": 0, "nearby_event_minute": 10, "train_interval": 4,
                          "train_capacity": 1000, "close_vermont": False, "close_exposition": False}
        self._compare = None       # /compare response

        self._window = ui.Window("ODT-CC Command Center", width=460, height=900)
        with self._window.frame:
            # no horizontal scrolling: rows must fit the panel width (wide rows otherwise push columns off-screen)
            with ui.ScrollingFrame(style={"background_color": PANEL},
                                   horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
                with ui.VStack(spacing=8, height=0, style={"margin": 2}):
                    self._top_frame = ui.Frame(build_fn=self._build_top)
                    self._plan_frame = ui.Frame(build_fn=self._build_plan)
                    self._incident_frame = ui.Frame(build_fn=self._build_incident_builder)
                    self._zones_frame = ui.Frame(build_fn=self._build_zones)

        self._timeline = omni.timeline.get_timeline_interface()
        self._tl_sub = self._timeline.get_timeline_event_stream().create_subscription_to_pop(
            self._on_timeline_event, name="odtcc.command_center")
        # The server keeps its approved plan across Kit restarts; start every session from "do nothing"
        self._spawn(self._reset())

        stage_path = settings.get(f"{SETTINGS}/open_stage") or str(TWIN_LIVE)
        self._spawn(self._open_stage_when_ready(stage_path))
        self._spawn(self._heartbeat())

    async def _heartbeat(self):
        """Ask for /state every second even while paused, so the web dashboard sees Kit as connected and
        its queued commands (play, approve, reset...) arrive promptly."""
        while True:
            await asyncio.sleep(1.0)
            if self._minute is not None:
                self._request_state()

    async def _open_stage_when_ready(self, path):
        # The editor ignores a stage on the command line and builds its template stage at app-ready.
        # Opening ours before RTX has rendered frames crashes the Fabric scene delegate, so wait for both.
        app = omni.kit.app.get_app()
        while not app.is_app_ready() or get_active_viewport() is None:
            await app.next_update_async()
        await next_viewport_frame_async(get_active_viewport(), n_frames=30)
        viewport_window = ui.Workspace.get_window("Viewport")
        if viewport_window and self._window:
            self._window.dock_in(viewport_window, ui.DockPosition.RIGHT, 0.4)
            self._window.focus()
        ctx = omni.usd.get_context()
        if Path(ctx.get_stage_url() or "").name != Path(path).name:
            await ctx.open_stage_async(path)

    def _spawn(self, coro):
        """Start a background task that on_shutdown cancels (a hot reload must not leave old tasks running)."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def on_shutdown(self):
        if self._tl_sub is not None:
            self._tl_sub.unsubscribe()
        self._tl_sub = None
        for task in list(self._tasks):
            task.cancel()
        if self._window:
            self._window.destroy()
            self._window = None

    # ------------------------------------------------------------------ timeline -> /state
    def _current_minute(self):
        return int(round(self._timeline.get_current_time() * self._timeline.get_time_codes_per_seconds()))

    def _on_timeline_event(self, _event):
        if not hasattr(self, "_tasks"):   # an instance already shut down by a hot reload
            return
        minute = self._current_minute()
        if self._resetting_to_zero:
            if minute != 0:               # the timeline hasn't applied the reset yet: ignore the old minute
                return
            self._resetting_to_zero = False
        if minute != self._minute:
            self._minute = minute
            self._request_state()

    def _request_state(self):
        if self._state_task and not self._state_task.done():
            self._state_dirty = True   # one fetch at a time; refetch the latest minute when it lands
            return
        self._state_task = self._spawn(self._fetch_state())

    async def _fetch_state(self):
        while True:
            self._state_dirty = False
            gen = self._gen
            try:
                st = await self._client.state(self._minute)
            except Exception as e:
                self._status = f"brain service offline: {e} (retrying)"
                self._refresh()
                await asyncio.sleep(2)
                continue
            if gen != self._gen:          # sent before a reset: its alerts belong to the old run
                continue
            if not self._client.mock:
                self._status = "connected"
            if self._levers is None:
                await self._load_levers()
            self._state = st
            if st.get("commands"):                # sent by the web dashboard; strictly in order
                self._cmd_task = self._spawn(self._run_commands(st["commands"], self._cmd_task))
            card_id = st.get("card_id")
            if card_id and (self._card or {}).get("card_id") != card_id and self._busy != "recommending":
                self._spawn(self._fetch_card())       # a recommendation requested from the dashboard
            reason = self._pause_reason(st)
            if reason and self._busy in (None, "recommending"):
                self._timeline.pause()
                # playback ran on while the server answered: go back to the minute the alert is for
                self._timeline.set_current_time(st["minute"] / self._timeline.get_time_codes_per_seconds())
                self._timeline.commit()
                self._minute = st["minute"]
                self._decided_alerts = {a["id"] for a in st["alerts"]}
                self._last_decision = st["minute"]
                self._status = f"paused: {reason}"
                if self._busy == "recommending":
                    self._recommend_again = st["minute"]   # the card in flight predates this: refresh after it
                else:
                    self._spawn(self._recommend(st["minute"]))
            # the 1 s heartbeat mostly returns the same state: only redraw on a real change (rebuilding
            # buttons every second would swallow clicks)
            fingerprint = json.dumps({k: v for k, v in st.items() if k != "commands"}, sort_keys=True, default=str)
            if fingerprint != self._last_fingerprint or reason:
                self._last_fingerprint = fingerprint
                self._refresh()
            if not self._state_dirty:
                return

    async def _run_commands(self, commands, previous):
        if previous and not previous.done():
            await previous                       # e.g. a reset must finish before the play that follows it
        for cmd in commands:
            await self._run_command(cmd)

    async def _run_command(self, cmd):
        """The web dashboard drives Kit's timeline and decisions through queued server commands."""
        action = cmd["action"]
        if action == "play":
            self._timeline.play()
        elif action == "pause":
            self._timeline.pause()
        elif action in ("reset", "scenario"):
            while self._busy:                     # e.g. a recommendation still in flight
                await asyncio.sleep(0.1)
            if action == "reset":
                await self._reset()
            else:
                await self._reset(cmd["id"], cmd.get("incidents"), cmd.get("planning"))
        elif action == "approved":
            self._approved = cmd.get("data") or self._approved
            self._card_used = True
            self._reload_live_layer()
            self._timeline.play()
        self._status = f"dashboard: {action}"
        self._refresh(plan=True)

    async def _fetch_card(self):
        try:
            card = await self._client.card()
        except Exception:
            return
        if (self._card or {}).get("card_id") != card.get("card_id"):
            self._card, self._card_used = card, False
            self._refresh(plan=True)

    def _pause_reason(self, st):
        """Pause on every newly reported incident; on newly alerting zones only if the operator hasn't just
        decided (5 min), so flickering alerts and routine updates don't stop the show."""
        new = [r for r in st.get("reports", []) if (r["id"], r["minute"], r["status"]) not in self._seen_reports]
        for r in new:
            self._seen_reports.add((r["id"], r["minute"], r["status"]))
        if any(r["status"] == "new" for r in new):
            return "new incident reported"
        fresh = {a["id"] for a in st["alerts"]} - self._decided_alerts
        if fresh and (self._last_decision is None or st["minute"] - self._last_decision >= 5):
            return "new alert at " + ", ".join(sorted(fresh))
        return None

    def _refresh(self, plan=False):
        """Redraw the per-minute parts; the 'Your plan' section only when asked, so dragging a slider isn't interrupted."""
        self._top_frame.rebuild()
        self._zones_frame.rebuild()
        if plan:
            self._plan_frame.rebuild()

    # ------------------------------------------------------------------ actions
    async def _load_levers(self):
        try:
            levers = await self._client.levers()
            scenarios = await self._client.scenarios()
        except Exception as e:
            self._status = f"levers unavailable: {e}"
            return
        # assign together: a redraw during the awaits above must not see levers without values
        self._my_plan = {l["key"]: l["default"] for l in levers}
        self._levers, self._scenarios = levers, scenarios
        self._plan_frame.rebuild()
        self._incident_frame.rebuild()

    async def _recommend(self, minute):
        self._busy = "recommending"
        self._card = None
        self._refresh()
        gen = self._gen
        try:
            card = await self._client.recommend(minute)
            if not card.get("stale") and gen == self._gen:   # computed for a run that has since been reset
                self._card, self._card_used = card, False
        except Exception as e:
            self._status = f"recommend failed: {e}"
        self._busy = None
        self._refresh(plan=True)
        again, self._recommend_again = self._recommend_again, None
        if again is not None and gen == self._gen:
            await self._recommend(again)

    async def _simulate_mine(self):
        if self._busy or self._minute is None:
            return
        self._busy = "simulating"
        self._refresh()
        try:
            self._my_result = await self._client.simulate(dict(self._my_plan), self._minute)
            self._my_stale = False
        except Exception as e:
            self._status = f"simulate failed: {e}"
        self._busy = None
        self._refresh(plan=True)

    async def _approve(self, plan):
        if self._busy:
            return
        self._busy = "approving"
        self._refresh()
        try:
            self._approved = await self._client.approve(plan, self._minute)
        except Exception as e:
            self._status = f"approve failed: {e}"
            self._busy = None
            self._refresh()
            return
        self._card_used = True
        self._reload_live_layer()
        self._busy = None
        self._refresh(plan=True)
        self._timeline.play()
        self._request_state()

    def _set_view(self, index):
        self._view = index
        self._refresh()

    def _keep_current_plan(self):
        """The operator decides not to change anything: dismiss the card and carry on."""
        self._card_used = True
        self._spawn(self._client.keep(self._minute))
        self._refresh()
        self._timeline.play()

    async def _inject(self):
        kinds = ["random"] + self._scenarios.get("incident_kinds", [])
        kind = kinds[self._inject_kind]
        try:
            await self._client.inject(self._minute, None if kind == "random" else kind)
        except Exception as e:
            self._status = f"inject failed: {e}"
        self._request_state()

    async def _reset(self, scenario_id=None, incidents=None, planning=None):
        """Back to minute 0 with nothing approved; optionally switch scenario (or apply custom incidents) first."""
        if self._busy:
            return
        self._busy = "switching scenario" if scenario_id else "resetting"
        self._gen += 1
        # from now on every request is for minute 0 of the new run (the heartbeat keeps polling while we wait)
        self._minute = 0
        self._resetting_to_zero = True
        self._refresh()
        try:
            if scenario_id:
                res = await self._client.set_scenario(scenario_id, incidents, planning)
                self._scenarios["current"] = scenario_id
                self._scenarios["current_description"] = res.get("description")
            else:
                await self._client.reset()
        except Exception as e:
            self._status = f"reset failed: {e}"
        self._reload_live_layer()
        self._card = self._approved = self._my_result = None
        self._card_used = False
        self._seen_reports, self._decided_alerts, self._last_decision = set(), set(), None
        self._busy = None
        self._timeline.stop()
        self._timeline.set_current_time(0)
        self._timeline.commit()          # apply now: Kit otherwise applies timeline changes on the next frame
        self._resetting_to_zero = self._current_minute() != 0
        self._minute = 0
        self._plan_frame.rebuild()
        self._request_state()

    def _reload_live_layer(self):
        stage = omni.usd.get_context().get_stage()
        layers = [l for l in (stage.GetUsedLayers() if stage else [])
                  if (l.realPath or "").endswith(CROWD_LIVE_NAME)]
        if not layers:
            if not self._client.mock:
                self._status = f"open {TWIN_LIVE.name} to see the plan in the viewport"
            return
        for layer in layers:
            layer.Reload(force=True)
        carb.log_info(f"[odtcc] reloaded {layers[0].realPath}")

    def _open_live_twin(self):
        self._spawn(omni.usd.get_context().open_stage_async(str(TWIN_LIVE)))

    # ------------------------------------------------------------------ UI
    def _build_top(self):
        with ui.VStack(spacing=8, height=0):
            self._build_header()
            self._build_banner()
            self._build_buttons()
            self._build_metrics()
            self._build_card()

    def _build_plan(self):
        if not self._levers:
            return
        with ui.VStack(spacing=8, height=0):
            with ui.CollapsableFrame("Your plan: try your own levers", height=0):
                with ui.VStack(spacing=4, height=0):
                    for lever in self._levers:
                        self._build_slider(lever)
                    with ui.HStack(height=32, spacing=6):
                        ui.Button("Simulate", clicked_fn=lambda: self._spawn(self._simulate_mine()))
                        ui.Button("Copy AI plan", width=110, enabled=bool(self._card), clicked_fn=self._copy_ai_plan)
                        ui.Button("Defaults", width=80, clicked_fn=self._default_plan)
                    self._result_frame = ui.Frame(build_fn=self._build_result)
            self._build_compare()

    def _build_slider(self, lever):
        key = lever["key"]
        with ui.HStack(height=24, spacing=6):
            ui.Label(lever["label"], width=200, elided_text=True, style={"color": WHITE},
                     tooltip=f"Owner: {lever['agency']} | normally {lever['default']}")
            if lever["integer"]:
                model = ui.SimpleIntModel(int(self._my_plan[key]))
                ui.IntSlider(model, min=lever["min"], max=lever["max"])
            else:
                model = ui.SimpleFloatModel(float(self._my_plan[key]))
                ui.FloatSlider(model, min=lever["min"], max=lever["max"], step=0.05, format="%.2f")
        model.add_value_changed_fn(lambda m, k=key, i=lever["integer"]: self._on_slider(k, m.as_int if i else round(m.as_float, 2)))

    def _on_slider(self, key, value):
        self._my_plan[key] = value
        if not self._my_stale:
            self._my_stale = True
            self._result_frame.rebuild()

    def _copy_ai_plan(self):
        self._my_plan = {l["key"]: self._card["plan"].get(l["key"], l["default"]) for l in self._levers}
        self._my_result, self._my_stale = None, False
        self._plan_frame.rebuild()

    def _build_incident_builder(self):
        if not self._scenarios:
            return
        inc, zones = self._inc, self._scenarios["zones"]
        types = [k for k, _ in INCIDENT_TYPES]
        ids = [z["id"] for z in zones]
        frame = ui.CollapsableFrame("Incident builder: invent a problem", height=0, collapsed=not self._inc_open)
        frame.set_collapsed_changed_fn(lambda collapsed: setattr(self, "_inc_open", not collapsed))
        with frame:
            with ui.VStack(spacing=4, height=0):
                self._combo_row("Type", [t for _, t in INCIDENT_TYPES], types.index(inc["type"]),
                                lambda i: self._set_inc("type", types[i], rebuild=True))
                self._combo_row("Zone", [z["label"] for z in zones], ids.index(inc["node"]) if inc["node"] in ids else 0,
                                lambda i: self._set_inc("node", ids[i]))
                self._int_row("Starts at minute", "from", 0, 90)
                self._int_row("Lasts (minutes)", "duration", 1, 60)
                if inc["type"] == "capacity":
                    self._int_row("Remaining capacity (%)", "remaining_pct", 0, 90)
                elif inc["type"] == "inflow":
                    lo, hi = self._scenarios["inflow_limits"]
                    self._int_row("Extra people per minute", "per_min", lo, hi)
                ui.Button("Apply incident (resets the run)", height=30, clicked_fn=self._apply_incident)

    def _combo_row(self, label, titles, index, on_pick):
        with ui.HStack(height=24, spacing=6):
            ui.Label(label, width=200, style={"color": WHITE})
            combo = ui.ComboBox(index, *titles)
        combo.model.add_item_changed_fn(lambda m, _item: on_pick(m.get_item_value_model().as_int))

    def _int_row(self, label, key, lo, hi):
        with ui.HStack(height=24, spacing=6):
            ui.Label(label, width=200, style={"color": WHITE})
            model = ui.SimpleIntModel(int(self._inc[key]))
            ui.IntSlider(model, min=lo, max=hi)
        model.add_value_changed_fn(lambda m: self._set_inc(key, m.as_int))

    def _set_inc(self, key, value, rebuild=False):
        self._inc[key] = value
        if rebuild:
            self._incident_frame.rebuild()

    def _apply_incident(self):
        i = self._inc
        inc = {"type": i["type"], "node": i["node"], "from": i["from"], "to": min(i["from"] + i["duration"], 120)}
        if i["type"] == "capacity":
            inc["factor"] = i["remaining_pct"] / 100
        elif i["type"] == "inflow":
            inc["per_min"] = i["per_min"]
        self._spawn(self._reset("custom", [inc]))

    def _default_plan(self):
        self._my_plan = {l["key"]: l["default"] for l in self._levers}
        self._my_result, self._my_stale = None, False
        self._plan_frame.rebuild()

    # ---------------------------------------------------------- planning mode: compare plans
    def _build_compare(self):
        with ui.CollapsableFrame("Compare plans (planning mode)", height=0):
            with ui.VStack(spacing=4, height=0):
                with ui.HStack(height=26, spacing=6):
                    ui.Button("Save sliders as Plan A", clicked_fn=lambda: self._save_plan("Plan A", dict(self._my_plan)))
                    ui.Button("...as Plan B", width=100, clicked_fn=lambda: self._save_plan("Plan B", dict(self._my_plan)))
                    ui.Button("Add AI plan", width=110, enabled=bool(self._card), clicked_fn=self._save_ai_plan)
                ui.Label("Planned event (applies to every plan compared)", style={"color": AMBER}, height=0)
                self._planning_int("Tickets sold", "attendance", 40000, 80000)
                self._planning_float("Held for a 2nd wave (share)", "stagger_share", 0.0, 0.5)
                self._planning_int("2nd wave leaves after (min)", "stagger_delay", 0, 30)
                self._planning_int("Nearby event crowd (people)", "nearby_event_people", 0, 20000)
                self._planning_int("Nearby event lets out at (min)", "nearby_event_minute", 0, 40)
                self._planning_int("Timetable: train every (min)", "train_interval", 2, 8)
                self._planning_int("Timetable: people per train", "train_capacity", 600, 1200)
                self._planning_check("Road closure: Vermont Ave (parking exits 40%)", "close_vermont")
                self._planning_check("Road closure: Exposition Blvd (shuttles 50%)", "close_exposition")
                saved = ", ".join(self._saved_plans) or "none yet (save the sliders, or add the AI's plan)"
                ui.Label(f"Saved: {saved}", word_wrap=True, style={"color": GREY, "font_size": 13}, height=0)
                with ui.HStack(height=30, spacing=6):
                    ui.Button("Compare", enabled=bool(self._saved_plans),
                              clicked_fn=lambda: self._spawn(self._run_compare()))
                    ui.Button("Clear", width=80, clicked_fn=self._clear_compare)
                    ui.Button("Run this event live", width=160,
                              clicked_fn=lambda: self._spawn(self._reset("planned", planning=dict(self._planning))))
                self._compare_frame = ui.Frame(build_fn=self._build_compare_table)

    def _planning_int(self, label, key, lo, hi):
        with ui.HStack(height=22, spacing=6):
            ui.Label(label, width=230, style={"color": WHITE, "font_size": 13})
            model = ui.SimpleIntModel(int(self._planning[key]))
            ui.IntSlider(model, min=lo, max=hi)
        model.add_value_changed_fn(lambda m: self._planning.__setitem__(key, m.as_int))

    def _planning_float(self, label, key, lo, hi):
        with ui.HStack(height=22, spacing=6):
            ui.Label(label, width=230, style={"color": WHITE, "font_size": 13})
            model = ui.SimpleFloatModel(float(self._planning[key]))
            ui.FloatSlider(model, min=lo, max=hi, step=0.05, format="%.2f")
        model.add_value_changed_fn(lambda m: self._planning.__setitem__(key, round(m.as_float, 2)))

    def _planning_check(self, label, key):
        with ui.HStack(height=22, spacing=6):
            model = ui.SimpleBoolModel(bool(self._planning[key]))
            ui.CheckBox(model, width=20, style={"background_color": 0xFF777777, "color": WHITE, "border_radius": 2})
            ui.Label(label, style={"color": WHITE, "font_size": 13})
        model.add_value_changed_fn(lambda m: self._planning.__setitem__(key, m.as_bool))

    def _save_plan(self, name, plan):
        self._saved_plans[name] = plan
        self._plan_frame.rebuild()

    def _save_ai_plan(self):
        self._save_plan(f"AI: {self._card['recommended'].replace('_', ' ')}", dict(self._card["plan"]))

    def _clear_compare(self):
        self._saved_plans, self._compare = {}, None
        self._plan_frame.rebuild()

    async def _run_compare(self):
        if self._busy:
            return
        self._busy = "comparing plans"
        self._refresh()
        try:
            self._compare = await self._client.compare(self._saved_plans, self._minute or 0, dict(self._planning))
        except Exception as e:
            self._status = f"compare failed: {e}"
        self._busy = None
        self._refresh()
        self._compare_frame.rebuild()

    def _build_compare_table(self):
        res = self._compare
        if not res:
            return
        rows = res["rows"]
        verdict_colour = {"SAFE": GREEN, "MARGINAL": AMBER, "UNSAFE": RED}
        metrics = [
            ("Verdict", lambda r: r["verdict"]),
            ("Dangerous zone-min", lambda r: str(r["dangerous_zone_minutes"])),
            ("Zone-min above alert", lambda r: str(r["alert_zone_minutes"])),
            ("Worst bottleneck", lambda r: r["worst_zone"].split(" ")[0] + f" {r['peak_density']:.2f}"),
            ("Stadium clears", lambda r: _fmt_min(r["stadium_cleared_by_min"])),
            ("Trains / hour", lambda r: f"{r['trains_per_hour']:g}"),
            ("Extra buses", lambda r: str(r["extra_buses"])),
            ("Effort (0-5)", lambda r: f"{r['effort']}"),
            ("Approve by", lambda r: _fmt_min(r["approve_by_minute"])),
        ]
        with ui.VStack(spacing=2, height=0):
            ui.Label(f"From minute {res['minute']}: " + "; ".join(res.get("planning_notes") or
                     [f"{res['attendance']:,} tickets"]), word_wrap=True, height=0, style={"color": GREY, "font_size": 13})
            with ui.HStack(height=22):
                ui.Label("", width=140)
                for r in rows:
                    ui.Label(r["name"], elided_text=True, style={"color": WHITE, "font_size": 13})
            for label, fmt in metrics:
                with ui.HStack(height=20):
                    ui.Label(label, width=140, style={"color": GREY, "font_size": 13})
                    for r in rows:
                        colour = verdict_colour[r["verdict"]] if label == "Verdict" else WHITE
                        ui.Label(fmt(r), elided_text=True, style={"color": colour, "font_size": 13})
            ui.Label("Peak density (danger at 5)", style={"color": GREY, "font_size": 13}, height=0)
            for r in rows:
                peak = min(r["peak_density"], 6.0)
                with ui.HStack(height=16, spacing=4):
                    ui.Label(r["name"], width=140, elided_text=True, style={"color": WHITE, "font_size": 12})
                    with ui.HStack():
                        ui.Rectangle(width=ui.Fraction(max(peak, 0.05)),
                                     style={"background_color": verdict_colour[r["verdict"]]})
                        ui.Spacer(width=ui.Fraction(6.0 - peak))
                    ui.Label(f"{r['peak_density']:.2f}", width=40, style={"color": WHITE, "font_size": 12})
            with ui.HStack(height=26, spacing=4):
                ui.Label("", width=140)
                for r in rows:
                    if r["actions"] == ["No change"]:
                        ui.Spacer()
                        continue
                    can = not self._busy and r["verdict"] != "UNSAFE"
                    ui.Button("Approve", enabled=can, clicked_fn=lambda plan=r["plan"]: self._spawn(self._approve(plan)),
                              style={"background_color": verdict_colour[r["verdict"]] if can else 0xFF444444})

    def _build_result(self):
        r = self._my_result
        if self._my_stale:
            ui.Label("Levers changed: press Simulate", style={"color": GREY}, height=0)
            return
        if not r:
            ui.Label(f"Scores the plan as if approved now (minute {self._minute}).",
                     style={"color": GREY, "font_size": 13}, height=0, word_wrap=True)
            return
        s, c = r["score"], r["cost"]
        colour = {"SAFE": GREEN, "MARGINAL": AMBER, "UNSAFE": RED}[r["verdict"]]
        with ui.VStack(spacing=3, height=0):
            if r["verdict"] == "UNSAFE":
                fd = r["first_danger"]
                head = f"UNSAFE: {s['dangerous_zone_minutes']} dangerous zone-minutes, first at {fd['label']} (minute {fd['minute']})"
            elif r["verdict"] == "MARGINAL":
                head = f"MARGINAL: no danger, but peak {s['peak_density']:.2f} is close to 5"
            else:
                head = f"SAFE: no dangerous zone-minutes, peak {s['peak_density']:.2f}"
            self._banner(head, colour)
            rows = [("Peak density", f"{s['peak_density']:.2f} at {r['peak_zone_label']}"),
                    ("Stadium cleared by", _fmt_min(s["stadium_cleared_by_min"])),
                    ("Effort (0-5)", f"{c['effort']}"),
                    ("Approve by", _fmt_min(r["approve_by_minute"]))]
            if self._card:
                e = self._card["expected"]
                rows.append((f"AI pick ({self._card['recommended'].replace('_', ' ')})",
                             f"peak {e['peak_density']:.2f}, clears {_fmt_min(e['stadium_cleared_by_min'])}, effort {e['effort_0_to_5']}"))
            for name, value in rows:
                with ui.HStack(height=20):
                    ui.Label(name, width=150, style={"color": GREY})
                    ui.Label(value, style={"color": WHITE}, elided_text=True)
            for a in r["actions"]:
                ui.Label(f"-  {a}", word_wrap=True, style={"color": WHITE}, height=0)
            can = not self._approved and not self._busy and r["verdict"] != "UNSAFE"
            ui.Button("Approve my plan" if r["verdict"] != "UNSAFE" else "Unsafe: can't approve", height=32,
                      enabled=can, clicked_fn=lambda: self._spawn(self._approve(r["plan"])),
                      style={"background_color": colour if can else 0xFF444444})

    def _build_header(self):
        m = "-" if self._minute is None else self._minute
        with ui.HStack(height=0, spacing=8):
            ui.Label(f"Minute {m}", style={"font_size": 28, "color": WHITE}, width=0)
            ui.Label("after the final whistle", style={"color": GREY}, alignment=ui.Alignment.LEFT_BOTTOM)
        status = self._status + (f"  |  {self._busy}..." if self._busy else "")
        ui.Label(status, style={"color": GREY, "font_size": 13}, word_wrap=True, height=0)
        with ui.HStack(height=26, spacing=6):
            ui.Label("View as", width=70, style={"color": GREY})
            view_combo = ui.ComboBox(self._view, *VIEWS)
        view_combo.model.add_item_changed_fn(lambda m, _i: self._set_view(m.get_item_value_model().as_int))
        sensors = (self._state or {}).get("sensors")
        if sensors:
            offline = sensors["bluetooth_total"] - sensors["bluetooth_online"]
            train = sensors["last_train_minute"]
            text = (f"Sensors: Bluetooth {sensors['bluetooth_online']}/{sensors['bluetooth_total']} online  |  "
                    f"{sensors['ticket_scans']:,} scanned in  |  "
                    f"last train {'min ' + str(train) if train is not None else 'none yet'}")
            ui.Label(text, style={"color": AMBER if offline else GREY, "font_size": 13}, word_wrap=True, height=0)
        if self._scenarios:
            items = list(self._scenarios["scenarios"])
            if self._scenarios["current"] in ("custom", "planned"):
                title = {"custom": "Custom incident", "planned": "Planned event"}[self._scenarios["current"]]
                items.append({"id": self._scenarios["current"], "title": title,
                              "description": self._scenarios.get("current_description") or ""})
            ids = [s["id"] for s in items]
            current = ids.index(self._scenarios["current"]) if self._scenarios["current"] in ids else 0
            with ui.HStack(height=26, spacing=6):
                ui.Label("Scenario", width=70, style={"color": GREY})
                combo = ui.ComboBox(current, *[s["title"] for s in items])
            combo.model.add_item_changed_fn(
                lambda m, _item: self._on_scenario_picked(ids[m.get_item_value_model().as_int]))
            ui.Label(items[current]["description"], word_wrap=True, height=0,
                     style={"color": GREY, "font_size": 13})

    def _on_scenario_picked(self, scenario_id):
        if scenario_id != self._scenarios["current"]:
            self._spawn(self._reset(scenario_id))

    def _banner(self, text, colour):
        with ui.ZStack(height=44):
            ui.Rectangle(style={"background_color": colour, "border_radius": 4})
            ui.Label(text, alignment=ui.Alignment.CENTER, word_wrap=True,
                     style={"color": WHITE, "font_size": 16, "margin": 6})

    def _build_banner(self):
        st = self._state or {}
        for inc in st.get("incidents", []):
            if inc["status"] == "active":
                self._banner(f"INCIDENT at {inc['label']} (reported minute {inc['from']}, "
                             f"expected until about minute {inc['to']})", RED)
        alerts = st.get("alerts", [])
        if alerts:
            worst = max(alerts, key=lambda z: z["predicted_density"])
            if worst["alert_band"] == "danger":
                self._banner(f"ALERT: {worst['label']} predicted dangerous at minute {worst['predicted_minute']}", RED)
            else:
                self._banner(f"WARNING: {worst['label']} approaching danger "
                             f"({worst['predicted_density']} p/m²) by minute {worst['predicted_minute']}", AMBER)
        elif self._approved:
            self._banner(f"Plan in force since minute {self._approved['minute']}: no alerts", GREEN)

        reports = st.get("reports", [])
        if reports or st.get("live"):
            with ui.CollapsableFrame(f"Incident feed ({len(reports)})", height=0):
                with ui.VStack(spacing=2, height=0):
                    for r in reversed(reports[-6:]):
                        colour = {"new": RED, "still ongoing": AMBER, "cleared": GREEN}.get(r.get("status"), WHITE)
                        ui.Label(f"min {r['minute']}  {r['source']}: {r['report']}", word_wrap=True,
                                 style={"color": colour, "font_size": 13}, height=0)
                    if st.get("live") and self._scenarios:
                        kinds = ["random"] + self._scenarios.get("incident_kinds", [])
                        with ui.HStack(height=26, spacing=6):
                            combo = ui.ComboBox(self._inject_kind, *[k.replace("_", " ") for k in kinds])
                            ui.Button("Inject incident now", width=150, enabled=not self._busy,
                                      clicked_fn=lambda: self._spawn(self._inject()))
                        combo.model.add_item_changed_fn(
                            lambda m, _i: setattr(self, "_inject_kind", m.get_item_value_model().as_int))

        c = self._card
        if c and not self._card_used and self._minute is not None:
            if c["approve_by_minute"] is None:
                ui.Label("No fully safe plan exists any more: act now to limit the danger",
                         word_wrap=True, style={"color": RED, "font_size": 18}, height=0)
                return
            left = c["approve_by_minute"] - self._minute
            if left >= 0:
                text, colour = f"Approve by minute {c['approve_by_minute']}  ({left} min left)", AMBER
            else:
                text, colour = f"Approval deadline (minute {c['approve_by_minute']}) has passed", RED
            ui.Label(text, style={"color": colour, "font_size": 18}, height=0)

    def _build_zones(self):
        zones = (self._state or {}).get("zones")
        if not zones:
            return
        with ui.VStack(spacing=8, height=0):
            self._build_zone_table(zones)
            self._build_decision_log()

    def _build_decision_log(self):
        entries = (self._state or {}).get("audit", [])
        with ui.CollapsableFrame(f"Decision log ({len(entries)} latest)", height=0):
            with ui.VStack(spacing=2, height=0):
                colours = {"approval": GREEN, "keep": GREEN, "incident": RED, "incident_update": AMBER,
                           "recommendation": WHITE}
                for e in reversed(entries):
                    ui.Label(f"{e['time'][11:]}  min {e['minute']}  {e['text']}", word_wrap=True, height=0,
                             style={"color": colours.get(e["kind"], GREY), "font_size": 12})
                ui.Label("Full audit trail: data/audit_log.jsonl", style={"color": GREY, "font_size": 11}, height=0)

    def _build_zone_table(self, zones):
        horizon = zones[0]["predicted_minute"] - self._state["minute"]
        with ui.CollapsableFrame("Zones", height=0):
            with ui.VStack(spacing=2, height=0):
                with ui.HStack(height=20):
                    ui.Label("Zone", style={"color": GREY})
                    ui.Label("Now (est.)", width=60, style={"color": GREY})
                    ui.Label(f"+{horizon} min", width=90, style={"color": GREY})
                    ui.Label("conf.", width=40, style={"color": GREY})
                    ui.Label("", width=70)
                for z in sorted(zones, key=lambda z: -z["predicted_density"]):
                    colour = BAND_COLOUR[z["alert_band"]]
                    with ui.HStack(height=22):
                        ui.Label(z["label"], elided_text=True, style={"color": WHITE})
                        if z["density_now"] is None:
                            ui.Label("no data", width=60, style={"color": AMBER})
                        else:
                            ui.Label(f"{z['density_now']:.2f}", width=60, style={"color": WHITE})
                        ui.Label(f"{z['predicted_density']:.2f} +-{z.get('error_band', 0):.1f}", width=90,
                                 style={"color": colour})
                        conf = z.get("confidence", "high")
                        ui.Label(conf[0].upper(), width=40, tooltip=f"forecast confidence: {conf}",
                                 style={"color": {"high": GREEN, "medium": AMBER, "low": RED}[conf]})
                        ui.Label(z["alert_band"].upper(), width=70, style={"color": colour})
                ui.Label("people/m², estimated from Bluetooth counts (~30% of phones seen); danger at 5, alert at 4.5. "
                         "conf.: H sensors fine, M a missed reading, L sensors offline (forecast inferred from neighbours)",
                         word_wrap=True, style={"color": GREY, "font_size": 12}, height=0)

    def _build_card(self):
        c = self._card
        if not c:
            return
        title = "Recommendation" if not c.get("plans_in_force") else "Re-plan"
        with ui.CollapsableFrame(f"{title}: {c['recommended'].replace('_', ' ')}", height=0):
            with ui.VStack(spacing=4, height=0):
                ui.Label(f"source: {c['source']}" + (f" ({c['latency_s']} s)" if c.get("latency_s") else ""),
                         style={"color": GREY, "font_size": 12}, height=0)
                ui.Label(c["briefing"], word_wrap=True, style={"color": WHITE}, height=0)
                view = VIEWS[self._view]
                if view != "Everyone":
                    tasks = for_view(c["actions"], view)
                    ui.Label(f"Your tasks ({view})", style={"color": AMBER}, height=0)
                    for a in tasks or ["No tasks for you in this plan"]:
                        ui.Label(f"-  {a}", word_wrap=True, style={"color": WHITE}, height=0)
                for w in c["why"]:
                    colour = RED if w.startswith("ESCALATE") else GREEN
                    ui.Label(f"+  {w}", word_wrap=True, style={"color": colour}, height=0)
                ui.Label(c["reason"], word_wrap=True, style={"color": GREY}, height=0)
        with ui.CollapsableFrame("Options (scored 0-100)", height=0):
            with ui.VStack(spacing=6, height=0):
                for oid, o in sorted(c["options"].items(), key=lambda kv: -kv[1]["score_0_to_100"]):
                    self._build_option(oid, o, oid == c["recommended"])
                ui.Label("score = safety 45 + clear speed 20 + ease 20 + time to act 15",
                         style={"color": GREY, "font_size": 12}, height=0)

    def _build_option(self, oid, o, is_pick):
        border = GREEN if is_pick else 0xFF555555
        with ui.ZStack(height=0):
            ui.Rectangle(style={"background_color": 0xFF333333, "border_color": border, "border_width": 2,
                                "border_radius": 4})
            with ui.VStack(spacing=2, height=0, style={"margin": 6}):
                with ui.HStack(height=24):
                    ui.Label(oid.replace("_", " ") + ("   (AI pick)" if is_pick else ""),
                             style={"color": WHITE, "font_size": 16})
                    ui.Label(f"{o['score_0_to_100']}/100", width=70, alignment=ui.Alignment.RIGHT,
                             style={"color": GREEN if is_pick else WHITE, "font_size": 18})
                parts = o.get("score_parts", {})
                ui.Label("  ".join(f"{k.replace('_', ' ')} {v}" for k, v in parts.items()),
                         style={"color": GREY, "font_size": 12}, height=0)
                danger = o["dangerous_zone_minutes"]
                ui.Label(f"{'0 dangerous minutes' if not danger else str(danger) + ' dangerous minutes'}  |  "
                         f"peak {o['peak_density']:.2f}  |  clears {_fmt_min(o['stadium_cleared_by_min'])}  |  "
                         f"approve by {_fmt_min(o['approve_by_minute'])}",
                         word_wrap=True, style={"color": RED if danger else WHITE, "font_size": 13}, height=0)
                view = VIEWS[self._view]
                shown = for_view(o["actions"], view)
                if not shown:
                    ui.Label(f"(no tasks for {view})", style={"color": GREY, "font_size": 13}, height=0)
                for a in shown:
                    ui.Label(f"-  {a}", word_wrap=True, style={"color": WHITE, "font_size": 13}, height=0)
                can = not self._busy and not self._card_used
                ui.Button("Approve this option", height=26, enabled=can,
                          clicked_fn=lambda plan=o["levers"]: self._spawn(self._approve(plan)),
                          style={"background_color": (GREEN if is_pick else 0xFF555555) if can else 0xFF444444})

    def _build_buttons(self):
        c = self._card
        with ui.HStack(height=44, spacing=6):
            can_approve = bool(c) and not self._card_used and not self._busy and not c.get("no_change")
            label = "Approve AI plan" if not (c and c.get("no_change")) else "No change needed"
            ui.Button(label, enabled=can_approve,
                      clicked_fn=lambda: self._spawn(self._approve(self._card["plan"])),
                      style={"background_color": GREEN if can_approve else 0xFF444444, "font_size": 18})
            if c and not self._card_used and c.get("plans_in_force"):
                ui.Button("Keep current plan", width=150, clicked_fn=self._keep_current_plan)
            if not c and not self._busy and self._minute is not None:
                ui.Button("Recommend now", width=130,
                          clicked_fn=lambda: self._spawn(self._recommend(self._minute)))
            ui.Button("Reset", width=80, clicked_fn=lambda: self._spawn(self._reset()))
        ui.Button("Open live twin", height=24, clicked_fn=self._open_live_twin)

    def _build_metrics(self):
        if self._card and not self._card_used:
            c = self._card
            before, after, title, base = c["do_nothing"], c["expected"], "If approved now", c.get("baseline_label")
        elif self._approved:
            a = self._approved
            before, after, title, base = a["baseline_score"], a["score"], "Approved plan", a.get("baseline_label")
        else:
            return
        base = (base or "doing nothing").replace("keeping the current plan", "keep current").capitalize()
        rows = [("Dangerous zone-minutes", "dangerous_zone_minutes", str),
                ("Peak density (p/m²)", "peak_density", lambda v: f"{v:.2f}"),
                ("Stadium cleared by", "stadium_cleared_by_min", _fmt_min)]
        with ui.CollapsableFrame("Before / after", height=0):
            with ui.VStack(spacing=2, height=0):
                with ui.HStack(height=20):
                    ui.Label("")
                    ui.Label(base, width=90, style={"color": GREY})
                    ui.Label(title, width=110, style={"color": GREY})
                for name, key, fmt in rows:
                    with ui.HStack(height=24):
                        ui.Label(name, style={"color": WHITE})
                        ui.Label(fmt(before[key]), width=90, style={"color": RED, "font_size": 18})
                        ui.Label(fmt(after[key]), width=110, style={"color": GREEN, "font_size": 18})
