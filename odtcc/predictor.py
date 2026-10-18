"""
ODT-CC — Step 4: crowd-density forecaster.

Learns to predict every zone's density HORIZON minutes ahead from what the
operator can see *now* (current + recent densities, people still in the stadium,
the plan in force). Trained on hundreds of simulated egress scenarios with
varied crowd sizes, randomness and interventions. Runs on GPU (XGBoost CUDA)
when available, CPU otherwise.

  python -m odtcc.predictor train     # simulate scenarios, train, evaluate, save model
  python -m odtcc.predictor demo      # replay "do nothing" and show when alerts fire

Outputs: models/forecaster.json, models/forecaster_meta.json
Import:  from odtcc.predictor import Forecaster
"""
import copy
import json
import os
import random
import sys
import time

import numpy as np
import pandas as pd
import xgboost as xgb

from odtcc.generate_data import load_venue
from odtcc.paths import MODEL_DIR
from odtcc.sensors import observe
from odtcc.scenarios import APPROVAL_MINUTE, DEFAULTS, DEMO_PLAN, DO_NOTHING, LIMITS, load_scenarios, run, venue_for

HORIZON = 15            # minutes ahead to predict
DANGER = 5.0            # people/m² = dangerous
ALERT_AT = 4.5          # alert when predicted density crosses this (errs on the side of safety)
LAGS = (1, 5)           # also look at densities this many minutes ago
N_SCENARIOS = 600
DEFAULT_LEVERS = DEFAULTS


# ------------------------------------------------------------------ features
def zones_of(venue):
    return [n["id"] for n in venue["nodes"] if "area_m2" in n]


def levers_of(plan):
    lv = dict(DEFAULT_LEVERS)
    lv.update({k: v for k, v in plan.items() if v is not None})
    return lv


def _shift(frame, n):
    """Values n minutes earlier; before minute 0 the venue was empty (offline sensors stay NaN)."""
    out = frame.shift(n)
    out.iloc[:n] = 0.0
    return out


def features(sim, zones, plan, start_minute=0, truth=None):
    """One row per (minute, zone): what an operator knows at that minute.

    sim is what the operator sees (sensor estimates; NaN = sensor offline, which XGBoost treats
    as missing). truth, when given, supplies the target (the real density HORIZON minutes later).
    Lever features show what's in force at each minute: DEFAULTS before the plan's
    start_minute (approval minute), the plan's values from then on.
    """
    dens = sim.pivot(index="minute", columns="node", values="density")[zones].astype(float)
    stadium = sim[sim["node"] == "stadium"].set_index("minute")["occupancy"]
    target = dens if truth is None else truth.pivot(index="minute", columns="node", values="density")[zones]

    wide = dens.add_prefix("d_")
    for lag in LAGS:
        wide = wide.join(_shift(dens, lag).add_prefix(f"d{lag}_"))
    wide["stadium_left"] = stadium
    wide["minute"] = wide.index
    active = wide.index >= start_minute
    for k, v in levers_of(plan).items():
        wide[f"lv_{k}"] = np.where(active, v, DEFAULT_LEVERS[k])

    rows = []
    for z_idx, z in enumerate(zones):
        part = wide.copy()
        part["zone"] = z_idx
        part["zone_now"] = dens[z]
        part["zone_trend5"] = dens[z] - _shift(dens[z], 5)
        part["target"] = target[z].shift(-HORIZON)  # NaN for the last HORIZON minutes
        part["zone_id"] = z
        rows.append(part)
    return pd.concat(rows).reset_index(drop=True)


FEATURE_EXCLUDE = {"target", "zone_id"}


# ------------------------------------------------------------------ training data
def random_plan(rng):
    if rng.random() < 0.35:
        return dict(DO_NOTHING)
    plan = {}
    for key, (lo, hi) in LIMITS.items():
        if rng.random() < 0.6:
            v = rng.uniform(lo, hi)
            plan[key] = int(round(v)) if key in ("stadium_release_per_min", "train_interval_min",
                                                 "shuttle_outflow_per_min") else round(v, 2)
    return plan


def random_incidents(rng, zones):
    """Disruptions mixed into training so the forecaster has seen trains stop, extra crowds and dead sensors."""
    out = []
    if rng.random() < 0.3:
        start, length = rng.randint(0, 40), rng.randint(5, 30)
        if rng.random() < 0.5:
            out.append({"type": "capacity", "factor": round(rng.uniform(0.0, 0.6), 2), "from": start, "to": start + length,
                        "node": rng.choice(["station_platform", "walkway_station", "shuttle_stop", "plaza_N"])})
        else:
            out.append({"type": "inflow", "per_min": rng.randint(150, 800), "from": start, "to": start + length,
                        "node": rng.choice(["walkway_station", "plaza_N", "station_platform", "plaza_S"])})
    if rng.random() < 0.3:
        start = rng.randint(0, 30)
        out.append({"type": "sensor_outage", "node": rng.choice(zones), "from": start, "to": start + rng.randint(10, 90)})
    return out


def build_dataset(n=N_SCENARIOS, seed=0):
    base = load_venue()
    zones = zones_of(base)
    rng = random.Random(seed)
    frames = []
    for i in range(n):
        venue = copy.deepcopy(base)
        venue["attendance"] = rng.randint(45000, 80000)
        venue["incidents"] = random_incidents(rng, zones)
        plan = random_plan(rng)
        # Plans are approved mid-event in practice, so most start after minute 0
        start = 0 if rng.random() < 0.3 else rng.randint(1, 30)
        sim = run(venue, plan, seed=rng.randint(0, 10**6), start_minute=start)
        observed, _ = observe(sim, venue, seed=rng.randint(0, 10**6))
        f = features(observed, zones, plan, start, truth=sim)
        f["scenario"] = i
        frames.append(f)
        if (i + 1) % 100 == 0:
            print(f"  simulated {i + 1}/{n} scenarios")
    data = pd.concat(frames).dropna(subset=["target"]).reset_index(drop=True)
    return data, zones


def has_gpu():
    import shutil
    import subprocess
    if not shutil.which("nvidia-smi"):
        return False
    return subprocess.run(["nvidia-smi", "-L"], capture_output=True).returncode == 0


def train():
    t0 = time.time()
    print(f"Simulating {N_SCENARIOS} training scenarios...")
    data, zones = build_dataset()
    feat_cols = [c for c in data.columns if c not in FEATURE_EXCLUDE | {"scenario"}]

    test_mask = data["scenario"] % 5 == 0  # hold out 20% of scenarios entirely
    tr, te = data[~test_mask], data[test_mask]
    device = "cuda" if has_gpu() else "cpu"
    print(f"Training on {len(tr):,} rows ({device}), testing on {len(te):,} unseen-scenario rows...")

    model = xgb.XGBRegressor(n_estimators=600, max_depth=8, learning_rate=0.05,
                             subsample=0.8, colsample_bytree=0.8,
                             tree_method="hist", device=device)
    model.fit(tr[feat_cols], tr["target"])

    pred = model.predict(te[feat_cols])
    actual = te["target"].to_numpy()
    mae = float(np.mean(np.abs(pred - actual)))
    busy = actual >= 2.0  # the rows that matter: crowded or worse
    mae_busy = float(np.mean(np.abs(pred[busy] - actual[busy])))
    will_be_danger = actual >= DANGER
    alerted = pred >= ALERT_AT
    tp = int((alerted & will_be_danger).sum())
    recall = tp / max(int(will_be_danger.sum()), 1)
    precision = tp / max(int(alerted.sum()), 1)

    os.makedirs(MODEL_DIR, exist_ok=True)
    model.save_model(f"{MODEL_DIR}/forecaster.json")
    meta = {"zones": zones, "features": feat_cols, "horizon_min": HORIZON,
            "danger": DANGER, "alert_at": ALERT_AT, "device": device,
            "test": {"mae": round(mae, 3), "mae_busy": round(mae_busy, 3), "recall": round(recall, 3), "precision": round(precision, 3),
                     "rows": int(len(te))}}
    json.dump(meta, open(f"{MODEL_DIR}/forecaster_meta.json", "w"), indent=2)

    print(f"\nHeld-out scenarios ({len(te):,} predictions, {HORIZON} min ahead):")
    print(f"  mean error            {mae:.2f} people/m²  (all zones)")
    print(f"  mean error when busy  {mae_busy:.2f} people/m²  (zones at 2+ people/m²)")
    print(f"  dangers caught        {recall:.0%}  (recall)")
    print(f"  alerts that were real {precision:.0%}  (precision)")
    print(f"Saved {MODEL_DIR}/forecaster.json  ({time.time() - t0:.0f}s total)")


# ------------------------------------------------------------------ inference
class Forecaster:
    def __init__(self, model_dir=MODEL_DIR):
        self.meta = json.load(open(f"{model_dir}/forecaster_meta.json"))
        self.model = xgb.XGBRegressor()
        self.model.load_model(f"{model_dir}/forecaster.json")
        self.model.set_params(device="cpu")  # live forecasts are a handful of rows; avoids GPU/CPU data mismatch
        self.zones = self.meta["zones"]

    def forecast(self, sim_so_far, plan=DO_NOTHING, start_minute=0):
        """sim_so_far: simulator rows up to 'now'; plan in force from start_minute.
        Returns per-zone prediction for now + HORIZON."""
        now = int(sim_so_far["minute"].max())
        f = features(sim_so_far, self.zones, plan, start_minute)
        f = f[f["minute"] == now]
        preds = self.model.predict(f[self.meta["features"]])
        out = []
        for zone, now_d, p in zip(f["zone_id"], f["zone_now"], preds):
            p = max(0.0, float(p))
            out.append({"zone": zone, "minute_now": now,
                        "density_now": None if np.isnan(now_d) else round(float(now_d), 2),  # None = sensor offline
                        "predicted_minute": now + HORIZON, "predicted_density": round(p, 2),
                        "alert": p >= self.meta["alert_at"]})
        return sorted(out, key=lambda r: -r["predicted_density"])


def lead_times(fc, venue):
    """First alert vs first real danger per zone, forecasting from the sensor feeds (do nothing)."""
    sim = run(venue, DO_NOTHING)
    observed, _ = observe(sim, venue)
    first_alert, first_danger = {}, {}
    for z in fc.zones:
        d = sim[(sim["node"] == z) & (sim["density"] >= DANGER)]
        if len(d):
            first_danger[z] = int(d["minute"].min())
    for t in range(0, 120 - HORIZON):
        for r in fc.forecast(observed[observed["minute"] <= t]):
            if r["alert"] and r["zone"] not in first_alert:
                first_alert[r["zone"]] = t
    return first_alert, first_danger


def demo():
    fc = Forecaster()
    for sid in load_scenarios():
        venue = venue_for(sid)
        labels = {n["id"]: n["label"] for n in venue["nodes"]}
        first_alert, first_danger = lead_times(fc, venue)
        print(f"\n'{sid}', do nothing, forecast from sensor feeds ({HORIZON} min ahead)")
        print(f"{'zone':<34}{'first alert':>12}{'danger starts':>15}{'warning':>10}")
        for z in sorted(set(first_alert) | set(first_danger), key=lambda z: first_danger.get(z, 999)):
            a, d = first_alert.get(z), first_danger.get(z)
            lead = f"{d - a} min" if a is not None and d is not None else "-"
            print(f"{labels[z]:<34}{str(a) if a is not None else 'none':>12}{str(d) if d is not None else 'never':>15}{lead:>10}")

    # After approval the forecaster must follow the plan now in force, not keep crying wolf
    venue = venue_for("normal")
    sim = run(venue, DEMO_PLAN, start_minute=APPROVAL_MINUTE)
    observed, _ = observe(sim, venue)
    alerts, errors = 0, []
    for t in range(APPROVAL_MINUTE, 120 - HORIZON):
        actual = sim[sim["minute"] == t + HORIZON].set_index("node")["density"]
        for r in fc.forecast(observed[observed["minute"] <= t], DEMO_PLAN, APPROVAL_MINUTE):
            alerts += r["alert"]
            errors.append(abs(r["predicted_density"] - actual[r["zone"]]))
    print(f"\nDemo plan approved at minute {APPROVAL_MINUTE}: {alerts} alerts afterwards "
          f"(actual dangerous minutes: 0), mean forecast error {np.mean(errors):.2f} people/m²")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"
    {"train": train, "demo": demo}[cmd]()