"""
ODT-CC — synthetic sensor feeds: hidden truth -> what the command center actually sees.

The simulator is "reality". Nothing downstream of /state reads it directly; it reads these feeds:

  Ticket scans        people who entered (exact to ~0.2%): where the 70,000 really comes from
  Gate exit counters  people still inside the bowl (~1% error)
  Bluetooth/Wi-Fi     per-zone phone counts: ~30% of people detected, +-8% noise from phone ID
                      randomization, ~2% of minutes missed. Aggregate counts only, no individuals.
                      Zones under a sensor_outage incident report nothing (NaN).
  Train GPS           departure minutes at Expo Park/USC (GTFS-realtime style)

Estimator: count / detection rate / area, averaged over the last 3 minutes; a missed minute
carries the last value forward; offline zones stay NaN and the forecaster infers them from the
zones around them (it is trained on data with random outages).
"""
import numpy as np
import pandas as pd

from odtcc.generate_data import level_for

DETECTION_RATE = 0.30
ID_NOISE = 0.08
DROPOUT = 0.02
SMOOTH_MINUTES = 3
SCAN_ERROR = 0.002
EXIT_COUNTER_ERROR = 0.01


def observe(sim, venue, seed=7):
    """Returns (observed, feeds).

    observed: same rows as sim, but zone density/occupancy are sensor estimates (NaN when offline),
              plus a 'sensor' column: ok | missed | offline (stadium: counted).
    feeds:    ticket_scans, train_departures (minutes), outages (incident list).
    """
    rng = np.random.default_rng(seed)
    zones = [n for n in venue["nodes"] if "area_m2" in n]
    occ = sim.pivot(index="minute", columns="node", values="occupancy")
    minutes = occ.index.to_numpy()
    outages = [i for i in venue.get("incidents", []) if i["type"] == "sensor_outage"]

    est_occ, status = {}, {}
    for z in zones:
        nid = z["id"]
        true = occ[nid].to_numpy(dtype=float)
        counts = rng.binomial(true.astype(int), DETECTION_RATE) * (1 + rng.normal(0, ID_NOISE, len(true)))
        raw = pd.Series(np.maximum(counts, 0) / DETECTION_RATE, index=minutes)
        missed = rng.random(len(true)) < DROPOUT
        offline = np.zeros(len(true), dtype=bool)
        for o in outages:
            if o["node"] == nid:
                offline |= (minutes >= o["from"]) & (minutes < o["to"])
        raw[missed | offline] = np.nan
        # carry the last reading forward; before the very first reading the zone is assumed empty
        smooth = raw.rolling(SMOOTH_MINUTES, min_periods=1).mean().ffill().fillna(0.0)
        smooth[offline] = np.nan
        est_occ[nid] = smooth
        status[nid] = np.where(offline, "offline", np.where(missed, "missed", "ok"))

    inside = occ["stadium"].to_numpy(dtype=float) * (1 + rng.normal(0, EXIT_COUNTER_ERROR, len(minutes)))
    areas = {z["id"]: z["area_m2"] for z in zones}
    obs = sim.copy()
    for nid, series in est_occ.items():
        mask = obs["node"] == nid
        values = series.reindex(obs.loc[mask, "minute"]).to_numpy()
        obs.loc[mask, "occupancy"] = np.round(values)
        obs.loc[mask, "density"] = np.round(values / areas[nid], 2)
        obs.loc[mask, "sensor"] = pd.Series(status[nid], index=minutes).reindex(obs.loc[mask, "minute"]).to_numpy()
    stad = obs["node"] == "stadium"
    obs.loc[stad, "occupancy"] = np.round(pd.Series(np.maximum(inside, 0), index=minutes)
                                          .reindex(obs.loc[stad, "minute"]).to_numpy())
    obs.loc[stad, "sensor"] = "counted"
    obs["level"] = [level_for(d) if pd.notna(d) else None for d in obs["density"]]

    platform = sim[(sim["node"] == "station_platform") & (sim["outflow"] > 0)]
    feeds = {
        "ticket_scans": int(round(venue["attendance"] * (1 + rng.normal(0, SCAN_ERROR)))),
        "train_departures": [int(m) for m in platform["minute"]],
        "outages": outages,
    }
    return obs, feeds


def sensor_summary(observed, feeds, minute):
    """What the panel's sensor strip shows at this minute."""
    now = observed[(observed["minute"] == minute) & (observed["sensor"].isin(["ok", "missed", "offline"]))]
    trains = [m for m in feeds["train_departures"] if m <= minute]
    return {
        "bluetooth_total": int(len(now)),
        "bluetooth_online": int((now["sensor"] != "offline").sum()),
        "bluetooth_missed_now": int((now["sensor"] == "missed").sum()),
        "offline_zones": now.loc[now["sensor"] == "offline", "node"].tolist(),
        "ticket_scans": feeds["ticket_scans"],
        "last_train_minute": trains[-1] if trains else None,
        "detection_rate": DETECTION_RATE,
    }
