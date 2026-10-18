"""
ODT-CC — Step 5: plan optimizer.

When an alert fires at minute t, sweep every combination of operator levers (within
LIMITS), simulate each one approved at minute t, and pick three safe options with
different trade-offs for the operator and the agent to explain:

  least_effort   safe with the fewest/smallest interventions
  most_margin    safe with the lowest peak density (most headroom below danger)
  fastest_clear  safe and empties the stadium soonest

"Safe" = 0 dangerous zone-minutes. Each pick also gets its approval deadline and a
robustness check (dangerous minutes across several noise seeds).

Run:  python -m odtcc.optimizer              (alert minute = APPROVAL_MINUTE)
      python -m odtcc.optimizer --minute 5
Out:  data/optimizer_result.json
"""
import argparse
import itertools
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

from odtcc.generate_data import load_venue
from odtcc.paths import DATA_DIR
from odtcc.scenarios import (APPROVAL_MINUTE, DEFAULTS, DEMO_PLAN, DO_NOTHING, approval_deadline,
                             cost, run, run_log, score, venue_for)

# Lever values to try (defaults included, so "leave this lever alone" is always an option)
GRID = {
    "stadium_release_per_min": [2500, 2000, 1600, 1200, 1000, 800],
    "train_interval_min": [4, 3, 2],
    "shuttle_outflow_per_min": [150, 225, 300, 375, 450],
    "north_to_shuttle_share": [0.4, 0.5, 0.6, 0.7, 0.8],
    "east_to_south_share": [0.0, 0.2, 0.4, 0.6, 0.8],
}
ROBUSTNESS_SEEDS = [1, 2, 3, 4, 5]
def result_file(scenario="normal"):
    return DATA_DIR / ("optimizer_result.json" if scenario == "normal" else f"optimizer_result_{scenario}.json")

_venue = None  # per-worker copy, set by _init


def _init(venue):
    global _venue
    _venue = venue


def candidate_plans():
    """Every grid combination, written with only the levers that differ from DEFAULTS."""
    keys = list(GRID)
    for values in itertools.product(*GRID.values()):
        yield {k: v for k, v in zip(keys, values) if v != DEFAULTS[k]}


def evaluate(args):
    plan, minute, base_plans = args
    s = score(run(_venue, plan, start_minute=minute, base_plans=base_plans), from_minute=minute)
    return {"plan": plan, "score": s, "cost": cost(plan)}


def pick_options(results):
    """Three safe options with different trade-offs. If nothing is fully safe, the same three
    trade-offs among the plans with the fewest dangerous zone-minutes (the caller flags it)."""
    safe = [r for r in results if r["score"]["dangerous_zone_minutes"] == 0]
    least_danger = min(r["score"]["dangerous_zone_minutes"] for r in results)
    pool = safe or [r for r in results if r["score"]["dangerous_zone_minutes"] == least_danger]
    clear = lambda r: r["score"]["stadium_cleared_by_min"] or 999
    effort = lambda r: r["cost"]["effort"]
    peak = lambda r: r["score"]["peak_density"]
    return {
        "least_effort": min(pool, key=lambda r: (effort(r), peak(r), clear(r))),
        "most_margin": min(pool, key=lambda r: (peak(r), effort(r), clear(r))),
        "fastest_clear": min(pool, key=lambda r: (clear(r), effort(r), peak(r))),
    }, safe


def robustness(venue, plan, minute, base_plans=()):
    """Dangerous zone-minutes under different random noise: does the plan hold up?"""
    return [score(run(venue, plan, start_minute=minute, seed=s, base_plans=base_plans), minute)["dangerous_zone_minutes"]
            for s in ROBUSTNESS_SEEDS]


def optimize(minute=APPROVAL_MINUTE, venue=None, workers=None, base_plans=()):
    """Sweep every lever combination approved at `minute`, on top of base_plans (approvals already
    in force). The baseline is "keep what's in force": do nothing, or the current plan."""
    venue = venue or load_venue()
    base_plans = [(m, p) for m, p in base_plans if m < minute]
    t0 = time.time()
    plans = list(candidate_plans())
    with ProcessPoolExecutor(max_workers=workers or min(32, os.cpu_count() or 1),
                             initializer=_init, initargs=(venue,)) as pool:
        results = list(pool.map(evaluate, [(p, minute, base_plans) for p in plans], chunksize=32))
    sweep_s = time.time() - t0

    options, safe = pick_options(results)
    for r in options.values():
        r["approval_deadline"] = approval_deadline(venue, r["plan"], from_minute=minute, base_plans=base_plans)
        r["robustness_danger_minutes"] = robustness(venue, r["plan"], minute, base_plans)

    baseline = score(run_log(venue, base_plans), from_minute=minute)
    demo = {"plan": DEMO_PLAN, "score": score(run(venue, DEMO_PLAN, start_minute=minute), minute),
            "cost": cost(DEMO_PLAN)}
    return {
        "alert_minute": minute,
        "scenario": venue.get("scenario", "normal"),
        "incidents": venue.get("incidents", []),
        "base_plans": [[m, p] for m, p in base_plans],
        "plans_evaluated": len(results),
        "safe_plans": len(safe),
        "no_fully_safe_plan": not safe,
        "sweep_seconds": round(sweep_s, 1),
        "baseline": baseline,
        "options": options,
        "demo_plan": demo,
    }


def _fmt_plan(plan):
    if not plan:
        return "do nothing"
    short = {"stadium_release_per_min": "release", "train_interval_min": "trains every",
             "shuttle_outflow_per_min": "shuttles", "north_to_shuttle_share": "N→shuttle",
             "east_to_south_share": "E→south"}
    return ", ".join(f"{short[k]} {v}" for k, v in plan.items())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minute", type=int, default=APPROVAL_MINUTE, help="approval minute to optimize for")
    ap.add_argument("--scenario", default="normal", help="scenario id from config/scenarios.json")
    args = ap.parse_args()

    out = optimize(args.minute, venue_for(args.scenario))
    out_file = result_file(args.scenario)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(out, f, indent=2)

    print(f"Approval at minute {out['alert_minute']}: {out['plans_evaluated']} plans simulated in "
          f"{out['sweep_seconds']}s, {out['safe_plans']} are safe (0 dangerous zone-minutes)")
    print(f"Do nothing: {out['baseline']['dangerous_zone_minutes']} dangerous zone-minutes, "
          f"peak {out['baseline']['peak_density']}\n")
    rows = [(name, r) for name, r in out["options"].items()] + [("demo_plan (manual)", out["demo_plan"])]
    print(f"{'option':<20}{'danger':>7}{'peak':>6}{'cleared':>8}{'effort':>7}{'deadline':>9}  robust  plan")
    for name, r in rows:
        s, c = r["score"], r["cost"]
        robust = r.get("robustness_danger_minutes")
        robust = f"{sum(x == 0 for x in robust)}/{len(robust)}" if robust else "-"
        print(f"{name:<20}{s['dangerous_zone_minutes']:>7}{s['peak_density']:>6}{s['stadium_cleared_by_min']:>8}"
              f"{c['effort']:>7}{str(r.get('approval_deadline', '-')):>9}  {robust:>6}  {_fmt_plan(r['plan'])}")
    print(f"\nSaved {os.path.relpath(out_file)}")


if __name__ == "__main__":
    main()
