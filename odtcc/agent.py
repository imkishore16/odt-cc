"""
ODT-CC — Step 6: Nemotron operator agent.

When an alert fires, the agent gathers facts with tools (forecast, optimizer options,
what-if simulation), picks one of the optimizer's SAFE options and writes a short
operator briefing. Code computes every number; the LLM only chooses and explains.

Guardrails
  - the action card's levers and numbers always come from the optimizer, never LLM text
  - the recommendation must be one of the optimizer's options (else template fallback)
  - simulate_plan only accepts levers within LIMITS
  - the agent never approves: requires_approval is always True; only the operator's click acts
Fallbacks
  - LLM unreachable / slow / malformed JSON  ->  template card from the same facts
  - ODTCC_LLM=cached                         ->  replay the last good LLM card (offline rehearsal)

Config (env vars)
  ODTCC_LLM            local (default) | hosted | cached | template
  ODTCC_LLM_BASE_URL   override endpoint (local: http://localhost:8001/v1, hosted: integrate.api.nvidia.com)
  ODTCC_LLM_MODEL      default nvidia/nemotron-3.5-lightning-30b-a3b
  ODTCC_LLM_THINKING   1 = reasoning mode on (default 0)
  NVIDIA_API_KEY / NGC_API_KEY   for hosted

Run:  python -m odtcc.agent                 (alert minute = APPROVAL_MINUTE)
      python -m odtcc.agent --minute 2
      ODTCC_LLM=template python -m odtcc.agent
"""
import argparse
import json
import os
import re
import time

from odtcc.generate_data import load_venue
from odtcc.optimizer import optimize, result_file
from odtcc.paths import DATA_DIR
from odtcc.predictor import Forecaster
from odtcc.sensors import observe
from odtcc.scenarios import APPROVAL_MINUTE, DEFAULTS, DO_NOTHING, LIMITS, apply_plan, cost, run, run_log, score, venue_for

MODEL = os.environ.get("ODTCC_LLM_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
ENDPOINTS = {"local": "http://localhost:8001/v1", "hosted": "https://integrate.api.nvidia.com/v1"}
def cache_file(scenario="normal"):
    return DATA_DIR / ("agent_cache.json" if scenario == "normal" else f"agent_cache_{scenario}.json")
CALL_TIMEOUT_S = 30      # per LLM call
THINKING = os.environ.get("ODTCC_LLM_THINKING", "0") == "1"   # Nemotron reasoning mode (slower, more careful)
MAX_TOOL_ROUNDS = 4
REPAIR_ROUNDS = 2       # retries when the briefing quotes numbers not in the facts
OPTION_IDS = ("least_effort", "most_margin", "fastest_clear")

LEVER_TEXT = {
    "stadium_release_per_min": lambda v: f"Hold crowd in the bowl: release {v:,} people/min (normally {DEFAULTS['stadium_release_per_min']:,})",
    "train_interval_min": lambda v: f"Run a train every {v} min (normally every {DEFAULTS['train_interval_min']})",
    "shuttle_outflow_per_min": lambda v: f"Add shuttle buses: {v} people/min (normally {DEFAULTS['shuttle_outflow_per_min']})",
    "north_to_shuttle_share": lambda v: f"Steer {v:.0%} of the north plaza to shuttles (normally {DEFAULTS['north_to_shuttle_share']:.0%})",
    "east_to_south_share": lambda v: f"Send {v:.0%} of east-gate exits south (normally {DEFAULTS['east_to_south_share']:.0%})",
}

# Who would carry out each lever in a shared command centre (likely owners, for the action card)
AGENCY = {
    "stadium_release_per_min": "LA28 Venue Ops",
    "train_interval_min": "LA Metro",
    "shuttle_outflow_per_min": "LA28 Transport / LADOT",
    "north_to_shuttle_share": "Venue stewards / LAPD",
    "east_to_south_share": "Venue stewards / LAPD",
}


# ------------------------------------------------------------------ facts (code, not LLM)
class Facts:
    """Everything the agent may say, computed by the simulator, predictor and optimizer."""

    def __init__(self, minute, venue=None, forecaster=None, base_plans=()):
        self.minute = minute
        self.venue = venue or load_venue()
        # approvals already in force [(minute, plan), ...]; re-planning happens on top of them
        self.base_plans = [(m, p) for m, p in base_plans if m <= minute]
        self.labels = {n["id"]: n["label"] for n in self.venue["nodes"]}
        self.forecaster = forecaster or Forecaster()
        self._opt = None
        self._forecast = None

    def forecast(self):
        if self._forecast is None:
            sim = run_log(self.venue, self.base_plans)   # the world so far, with what's been approved
            observed, _ = observe(sim, self.venue)      # what the sensors reported (same seed as /state)
            start, plan = self.base_plans[-1] if self.base_plans else (0, DO_NOTHING)
            rows = self.forecaster.forecast(observed[observed["minute"] <= self.minute], plan, start)
            self._forecast = [{"zone": r["zone"], "label": self.labels[r["zone"]],
                 "density_now": r["density_now"], "predicted_density": r["predicted_density"],
                 "predicted_minute": r["predicted_minute"], "alert": r["alert"]} for r in rows]
        return self._forecast

    @property
    def scenario(self):
        return self.venue.get("scenario", "normal")

    @property
    def base_label(self):
        """What the "no change" baseline means right now."""
        return "keeping the current plan" if self.base_plans else "doing nothing"

    def incident(self):
        """The known problem in plain words, or None (normal egress, or a live run with nothing reported)."""
        if self.scenario == "normal" or (self.scenario == "live" and not self.venue.get("incidents")):
            return None
        return {"scenario": self.venue["scenario_title"], "description": self.venue["scenario_description"]}

    def optimizer(self):
        if self._opt is None:
            f = result_file(self.scenario)
            cached = json.load(open(f)) if f.exists() else None
            fresh = (cached and cached["alert_minute"] == self.minute
                     and cached.get("incidents", []) == self.venue.get("incidents", [])
                     and cached.get("base_plans", []) == [[m, p] for m, p in self.base_plans])
            self._opt = cached if fresh else optimize(self.minute, self.venue, base_plans=self.base_plans)
        return self._opt

    def options(self):
        out = {}
        base_clear = self.optimizer()["baseline"]["stadium_cleared_by_min"]
        for oid, r in self.optimizer()["options"].items():
            total, parts = option_score(r, base_clear, self.minute)
            out[oid] = {"levers": r["plan"], "actions": describe_levers(r["plan"]),
                        "score_0_to_100": total, "score_parts": parts,
                        "dangerous_zone_minutes": r["score"]["dangerous_zone_minutes"],
                        "peak_density": r["score"]["peak_density"],
                        "peak_zone": self.labels[r["score"]["peak_zone"]],
                        "stadium_cleared_by_min": r["score"]["stadium_cleared_by_min"],
                        "effort_0_to_5": r["cost"]["effort"],
                        "extra_trains_per_hour": r["cost"]["extra_trains_per_hour"],
                        "extra_buses_est": r["cost"]["extra_buses_est"],
                        "approve_by_minute": r["approval_deadline"],
                        # derived numbers given explicitly, so the LLM never has to do arithmetic
                        "minutes_left_to_approve": (r["approval_deadline"] - self.minute
                                                    if r["approval_deadline"] is not None else None),
                        "extra_minutes_to_clear_vs_do_nothing": r["score"]["stadium_cleared_by_min"] - base_clear,
                        "safe_in_noise_runs": f"{sum(x == 0 for x in r['robustness_danger_minutes'])}/"
                                              f"{len(r['robustness_danger_minutes'])}"}
        return out

    def do_nothing(self):
        b = self.optimizer()["baseline"]
        return {"dangerous_zone_minutes": b["dangerous_zone_minutes"], "peak_density": b["peak_density"],
                "peak_zone": self.labels[b["peak_zone"]], "stadium_cleared_by_min": b["stadium_cleared_by_min"]}

    def simulate(self, levers):
        """What-if for the LLM: a custom plan approved now. Rejects anything outside LIMITS."""
        plan = {k: v for k, v in levers.items() if k in LIMITS and v is not None}
        try:
            apply_plan(self.venue, plan)
        except ValueError as e:
            return {"error": str(e), "limits": LIMITS}
        s = score(run(self.venue, plan, start_minute=self.minute, base_plans=self.base_plans), self.minute)
        return {"levers": plan, "dangerous_zone_minutes": s["dangerous_zone_minutes"],
                "peak_density": s["peak_density"], "stadium_cleared_by_min": s["stadium_cleared_by_min"],
                "effort_0_to_5": cost(plan)["effort"]}


SCORE_WEIGHTS = {"safety": 45, "speed": 20, "ease": 20, "time_to_act": 15}


def option_score(r, base_clear, minute):
    """0-100 score for an optimizer option, with its parts (all computed by code).

    safety      headroom under the danger line of 5 people/m² (0 if any dangerous minute)
    speed       how little it delays clearing the stadium vs the baseline (90 min later = 0)
    ease        1 - effort/5
    time_to_act minutes left to approve (10+ min = full marks)
    """
    s, c = r["score"], r["cost"]
    safety = 0.0 if s["dangerous_zone_minutes"] else max(0.0, (5 - s["peak_density"]) / 5)
    delay = (s["stadium_cleared_by_min"] or 120) - (base_clear or 0)
    speed = min(max(1 - delay / 90, 0.0), 1.0)
    ease = 1 - c["effort"] / 5
    left = r["approval_deadline"] - minute if r["approval_deadline"] is not None else 0
    time_to_act = min(max(left / 10, 0.0), 1.0)
    parts = {k: round(SCORE_WEIGHTS[k] * v) for k, v in
             {"safety": safety, "speed": speed, "ease": ease, "time_to_act": time_to_act}.items()}
    return sum(parts.values()), parts


def describe_levers(plan, tagged=True):
    return [(f"[{AGENCY[k]}] " if tagged else "") + LEVER_TEXT[k](v) for k, v in plan.items()] or ["No change"]


# ------------------------------------------------------------------ action card
def compare(facts, recommended):
    """Why-bullets and trade-offs for the recommended option, written by code so they're always right."""
    options, base = facts.options(), facts.do_nothing()
    o = options[recommended]
    others = {k: v for k, v in options.items() if k != recommended}
    rank = 1 + sum(v["peak_density"] < o["peak_density"] for v in others.values())
    margin = ["the lowest", "the second-lowest", "the highest"][rank - 1]
    if o["dangerous_zone_minutes"] == 0:
        if base["dangerous_zone_minutes"]:
            first = (f"Prevents all {base['dangerous_zone_minutes']} dangerous zone-minutes "
                     f"({facts.base_label} peaks at {base['peak_density']} people/m²)")
        else:
            first = (f"{facts.base_label.capitalize()} stays safe but peaks at {base['peak_density']} people/m²; "
                     f"this option peaks at {o['peak_density']}")
        why = [first,
               f"Peak {o['peak_density']} people/m² at {o['peak_zone']}: {margin} of the {len(options)} safe options",
               f"Held up in {o['safe_in_noise_runs']} noise runs; approve by minute {o['approve_by_minute']} "
               f"({o['minutes_left_to_approve']} min from now)"]
    else:
        why = [f"No fully safe plan exists from minute {facts.minute}: cuts dangerous zone-minutes from "
               f"{base['dangerous_zone_minutes']} to {o['dangerous_zone_minutes']}",
               f"Peak {o['peak_density']} people/m² at {o['peak_zone']}: {margin} of the {len(options)} best options",
               "Approve now: every minute of delay makes it worse"]
    trade_off = [f"Stadium clears at minute {o['stadium_cleared_by_min']} "
                 f"({facts.base_label}: minute {base['stadium_cleared_by_min']})"]
    for k, v in others.items():
        trade_off.append(f"vs {k.replace('_', ' ')}: clears at minute {v['stadium_cleared_by_min']}, "
                         f"peak {v['peak_density']}, effort {v['effort_0_to_5']} (this plan: {o['effort_0_to_5']})")
    return why, trade_off


def build_card(facts, recommended, briefing, reason, source, latency_s=None):
    """The only thing the UI shows. Numbers and comparisons come from code; the LLM (or the
    template) supplies only the choice, the briefing and a one-line reason."""
    options = facts.options()
    if recommended not in options:
        raise ValueError(f"recommended option {recommended!r} is not an optimizer option")
    rec = options[recommended]
    why, trade_off = compare(facts, recommended)
    current = facts.base_plans[-1][1] if facts.base_plans else None
    no_change = current is not None and rec["levers"] == current
    if no_change and rec["dangerous_zone_minutes"]:
        why.insert(0, f"ESCALATE: no lever combination beats the plan already in force, and "
                      f"{rec['dangerous_zone_minutes']} dangerous zone-minutes are still expected. Measures beyond "
                      f"these levers are needed (e.g. stop releasing from the bowl, police crowd management).")
    elif no_change:
        why.insert(0, "The plan already in force is still the best option: no change needed")
    alerts = [f for f in facts.forecast() if f["alert"]]
    return {
        "minute": facts.minute,
        "no_change": no_change,
        "scenario": facts.scenario,
        "incident": facts.incident(),
        "no_fully_safe_plan": facts.optimizer().get("no_fully_safe_plan", False),
        "source": source,
        "latency_s": latency_s,
        "alerts": alerts,
        "do_nothing": facts.do_nothing(),
        "baseline_label": facts.base_label,        # "doing nothing" or "keeping the current plan"
        "plans_in_force": [[m, p] for m, p in facts.base_plans],
        "recommended": recommended,
        "plan": rec["levers"],                  # what /approve will apply
        "actions": rec["actions"],
        "expected": {k: rec[k] for k in ("dangerous_zone_minutes", "peak_density", "peak_zone",
                                         "stadium_cleared_by_min", "effort_0_to_5")},
        "approve_by_minute": rec["approve_by_minute"],
        "briefing": briefing.strip(),
        "reason": reason.strip(),
        "why": why,
        "trade_off": trade_off,
        "options": options,
        "requires_approval": True,              # the agent never acts on its own
    }


def template_choice(options):
    """Rule-based pick: fastest clearing among options with headroom below the alert line."""
    roomy = [o for o in options if options[o]["peak_density"] < 4.5]
    pool = roomy or list(options)
    return min(pool, key=lambda o: (options[o]["stadium_cleared_by_min"] or 999, options[o]["effort_0_to_5"]))


def template_card(facts, source="template", reason=None):
    options = facts.options()
    rec = template_choice(options)
    o, base = options[rec], facts.do_nothing()
    alerts = [f for f in facts.forecast() if f["alert"]]
    worst = alerts[0] if alerts else None
    incident = facts.incident()
    head = (f"{incident['description']} " if incident else "")
    head += (f"{worst['label']} is forecast to reach {worst['predicted_density']} people/m² by minute "
             f"{worst['predicted_minute']}, approaching the danger level of 5. " if worst else "")
    deadline = (f"Approve by minute {o['approve_by_minute']}." if o["approve_by_minute"] is not None
                else "No fully safe plan exists any more; approve now to limit the danger.")
    briefing = (f"{head}{facts.base_label.capitalize()} leads to {base['dangerous_zone_minutes']} dangerous zone-minutes. "
                f"Recommended: {'; '.join(describe_levers(o['levers'], tagged=False)).lower()}. {deadline}")
    card = build_card(facts, rec, briefing,
                      "Rule-based choice: the fastest-clearing safe option that stays well under the alert line.",
                      source)
    if reason:
        card["fallback_reason"] = reason
    return card


# ------------------------------------------------------------------ LLM (Nemotron)
SYSTEM = """You are ODT-CC, the crowd-safety assistant in the LA28 Coliseum command center.
Context: the match has just ended and ~70,000 people are LEAVING the stadium (egress).
Danger = density of 5 people/m² or more. The forecaster alerts at 4.5 so operators get time to
act: a forecast of 4.5-4.99 means "approaching danger", NOT "above the threshold". Doing nothing
ends far worse than the 15-minute forecast shows (see do_nothing in get_plan_options).

Your job: recommend ONE of the optimizer's safe options to the human operator and explain it.
Rules:
- Use the tools to get facts. Never invent numbers; quote only numbers returned by tools,
  and when comparing options, quote each option's own values exactly. Do not calculate
  differences or sums yourself; use the derived fields the tools provide.
- You may only recommend one of these option ids: least_effort, most_margin, fastest_clear.
- Weigh trade-offs a real operator cares about: safety margin (peak density vs 5), how long
  people are held in the stadium, how much extra transit is needed, and the approval deadline.
- You never act: the operator must approve. Do not tell anyone to open entrances or delay entry.
- If the tools report an incident (e.g. a train delay or another event's crowd), say in one short
  phrase how the recommended plan copes with it.
- If no_fully_safe_plan is true, say plainly that no option avoids all danger and recommend the
  option that limits it best.
- A plan may already be in force (do_nothing_means = "keeping the current plan"). Then you are
  re-planning: say what changes compared with the current plan. Each option has a code-computed
  score_0_to_100; you may quote it, but weigh the trade-offs yourself.

When you have the facts, reply with ONLY this JSON (no markdown):
{"recommended": "<option id>",
 "briefing": "<2-3 plain sentences for the operator: the forecast risk, what the recommended plan does, the approval deadline. Describe ONLY the recommended plan; do not compare numbers across options (the screen shows that comparison).>",
 "reason": "<one sentence, no numbers: why this option is the right trade-off for the operator>"}"""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_forecast",
        "description": "15-minute-ahead crowd density forecast for every zone, at the current minute.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_plan_options",
        "description": "The optimizer's three safe plans (each approved now) plus the 'do nothing' outcome. "
                       "Includes actions, peak density, stadium clear time, effort, approval deadline.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "simulate_plan",
        "description": "What-if: simulate a custom combination of levers approved now. For checking ideas only; "
                       "you must still recommend one of the optimizer's options.",
        "parameters": {"type": "object", "properties": {
            k: {"type": "number", "description": f"range {lo}-{hi}"} for k, (lo, hi) in LIMITS.items()}}}},
]


def _client(mode):
    from openai import OpenAI
    base = os.environ.get("ODTCC_LLM_BASE_URL") or ENDPOINTS[mode]
    key = "not-needed" if mode == "local" else (os.environ.get("NVIDIA_API_KEY") or os.environ.get("NGC_API_KEY"))
    if not key:
        raise RuntimeError("hosted mode needs NVIDIA_API_KEY or NGC_API_KEY")
    return OpenAI(base_url=base, api_key=key, timeout=CALL_TIMEOUT_S, max_retries=0)


def _run_tool(facts, name, args):
    if name == "get_forecast":
        return {"minute_now": facts.minute, "incident": facts.incident(), "zones": facts.forecast()}
    if name == "get_plan_options":
        return {"incident": facts.incident(), "no_fully_safe_plan": facts.optimizer().get("no_fully_safe_plan", False),
                "do_nothing_means": facts.base_label,
                "do_nothing": facts.do_nothing(), "options": facts.options()}
    if name == "simulate_plan":
        return facts.simulate(args)
    return {"error": f"unknown tool {name}"}


CONSTANTS = {5, 4.5, 4.99, 15, 70000, 3}  # danger/alert lines (as in SYSTEM), horizon, crowd size, option count


def _numbers_in(obj, found=None):
    """Every number in a tool result (recursively), incl. numbers inside strings."""
    found = set() if found is None else found
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        found.add(float(obj))
    elif isinstance(obj, str):
        found.update(_numbers_text(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, found)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers_in(v, found)
    return found


def _numbers_text(text):
    # digits not glued to letters (skips "LA28"); commas allowed as thousands separators
    return {float(m.replace(",", "")) for m in re.findall(r"(?<![A-Za-z\d.])\d[\d,]*(?:\.\d+)?", text)}


def unsupported_numbers(out, allowed):
    """Numbers the LLM wrote that appear nowhere in the facts (also accepts shares written as %)."""
    ok = allowed | {round(a * 100, 6) for a in allowed if 0 < a < 1} | {float(c) for c in CONSTANTS}
    ok |= {round(a, 1) for a in ok} | {float(round(a)) for a in ok}   # honest rounding (1.37 -> 1.4)
    text = " ".join([str(out.get("briefing", "")), str(out.get("reason", ""))])
    return sorted(n for n in _numbers_text(text) if not any(abs(n - a) < 1e-6 for a in ok))


def _parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    return json.loads(m.group(0))


def llm_card(facts, mode="local", log=print):
    client = _client(mode)
    incident = facts.incident()
    situation = f" Incident: {incident['description']}" if incident else ""
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"It is minute {facts.minute} after the final whistle and the "
                                            f"forecaster has raised an alert.{situation} Brief me and recommend a plan."}]
    extra = {"chat_template_kwargs": {"enable_thinking": THINKING}}
    allowed = {float(facts.minute)} | _numbers_in(incident)
    t0 = time.perf_counter()
    for _ in range(MAX_TOOL_ROUNDS):
        r = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS, tool_choice="auto",
                                           temperature=0.2, max_tokens=700, extra_body=extra)
        msg = r.choices[0].message
        if not msg.tool_calls:
            break
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                        for c in msg.tool_calls]})
        for c in msg.tool_calls:
            args = json.loads(c.function.arguments or "{}")
            log(f"  tool: {c.function.name}({json.dumps(args) if args else ''})")
            result = _run_tool(facts, c.function.name, args)
            allowed |= _numbers_in(result)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})
    else:  # still calling tools: force an answer
        messages.append({"role": "user", "content": "Answer now with the JSON only."})
        msg = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.2,
                                             max_tokens=700, extra_body=extra).choices[0].message

    def check(reply):
        """(parsed, problem) — problem is None when the reply is valid JSON quoting only real numbers."""
        try:
            parsed = _parse_json(reply)
        except ValueError as e:  # includes json.JSONDecodeError
            return None, f"Your reply was not valid JSON ({e})."
        bad = unsupported_numbers(parsed, allowed)
        if bad:
            return parsed, (f"These numbers are not in the tool results: {bad}. Remove them or replace them "
                            "with values exactly as the tools gave them (no calculations).")
        return parsed, None

    out, problem = check(msg.content)
    for _ in range(REPAIR_ROUNDS):  # tell the model exactly what was wrong
        if not problem:
            break
        log(f"  guardrail: {problem} Asking Nemotron to correct")
        messages += [{"role": "assistant", "content": msg.content or ""},
                     {"role": "user", "content": problem + " Reply with the JSON only."}]
        msg = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.0,
                                             max_tokens=700, extra_body=extra).choices[0].message
        out, problem = check(msg.content)
    if problem:
        raise ValueError(f"after {REPAIR_ROUNDS} corrections: {problem}")
    if out.get("recommended") not in OPTION_IDS:
        raise ValueError(f"LLM recommended {out.get('recommended')!r}, not an optimizer option")
    return build_card(facts, out["recommended"], str(out.get("briefing", "")), str(out.get("reason", "")),
                      source=f"nemotron-{mode}", latency_s=round(time.perf_counter() - t0, 2))


# ------------------------------------------------------------------ entry point
def recommend(minute=APPROVAL_MINUTE, mode=None, facts=None, log=print):
    """Action card for the operator. Always returns a card (LLM, cached, or template)."""
    mode = mode or os.environ.get("ODTCC_LLM", "local")
    facts = facts or Facts(minute)
    if mode == "template":
        return template_card(facts)
    cache = cache_file(facts.scenario)

    def cached_card(reason=None):
        """The last good Nemotron card for this exact minute and incident, if one was saved."""
        if cache.exists():
            card = json.load(open(cache))
            if card.get("minute") == minute and card.get("incident") == facts.incident():
                card["source"] = "cached:" + card["source"]
                if reason:
                    card["fallback_reason"] = reason
                return card
        return None

    if mode == "cached":
        return cached_card() or template_card(facts, reason="no cached LLM card for this minute")
    try:
        card = llm_card(facts, mode, log)
    except Exception as e:  # any LLM failure -> saved card, else safe template; never a crash in front of the operator
        reason = f"{type(e).__name__}: {e}"
        log(f"  LLM failed ({reason}); using the saved card or the template")
        return cached_card(reason) or template_card(facts, reason=reason)
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(card, open(cache, "w"), indent=2)
    return card


def print_card(card):
    print(f"\n=== ODT-CC action card — minute {card['minute']}  [{card['source']}"
          f"{', %ss' % card['latency_s'] if card.get('latency_s') else ''}] ===")
    if card.get("fallback_reason"):
        print(f"(fallback: {card['fallback_reason']})")
    for a in card["alerts"]:
        print(f"ALERT  {a['label']}: {a['predicted_density']} people/m² forecast for minute {a['predicted_minute']}")
    print(f"\n{card['briefing']}\n")
    print(f"Recommended: {card['recommended']}  — approve by minute {card['approve_by_minute']}")
    for a in card["actions"]:
        print(f"  • {a}")
    e, b = card["expected"], card["do_nothing"]
    print(f"Expected: {b['dangerous_zone_minutes']} → {e['dangerous_zone_minutes']} dangerous zone-minutes, "
          f"peak {b['peak_density']} → {e['peak_density']}, stadium clears min {e['stadium_cleared_by_min']}")
    print(f"Reason: {card['reason']}")
    print("Why:")
    for w in card["why"]:
        print(f"  ✓ {w}")
    print("Trade-offs:")
    for t in card["trade_off"]:
        print(f"  – {t}")
    print("[ Approve ]  (operator decision — the agent never acts on its own)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minute", type=int, default=APPROVAL_MINUTE)
    ap.add_argument("--mode", choices=["local", "hosted", "cached", "template"], default=None)
    ap.add_argument("--scenario", default="normal", help="scenario id from config/scenarios.json")
    args = ap.parse_args()
    print_card(recommend(args.minute, args.mode, facts=Facts(args.minute, venue_for(args.scenario))))


if __name__ == "__main__":
    main()
