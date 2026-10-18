"""
ODT-CC — build the venue digital twin as layered OpenUSD.

  usd/layers/venue_layout.usda      static venue: ground, stadium, zones, paths, lights, camera
  usd/layers/crowd_<scenario>.usda  animated crowd data for one scenario (overrides only)
  usd/twin_<scenario>.usda          the file you open: layout + that scenario's crowd layer

Swap the crowd layer and the same venue shows a different future. In USD Composer
you can also do this live in the Layer panel.

Run:  python -m odtcc.build_twin                  (builds every data/egress_*.csv found)
      python -m odtcc.build_twin baseline plan    (only these scenarios)
Open: usd/twin_baseline.usda or usd/twin_plan.usda, then press Play (1 minute = 0.25 s)
Needs: pip install usd-core
"""
import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade

from odtcc.paths import DATA_DIR, LAYER_DIR, USD_DIR, VENUE_FILE, egress_csv

LAYOUT_FILE = LAYER_DIR / "venue_layout.usda"
CROWD_LIVE_FILE = LAYER_DIR / "crowd_live.usda"
TWIN_LIVE_FILE = USD_DIR / "twin_live.usda"
MINUTES_PER_SECOND = 4          # playback speed of the timeline
PILLAR_M_PER_DENSITY = 10.0     # 1 person/m² -> 10 m tall crowd pillar
MAX_DENSITY = 6.0
BOWL_TOP_M = 30.0               # height of the bowl cylinder
BOWL_FILL_M = 40.0              # full stadium -> 40 m tall "people inside" disc
BEACON_COLOUR = {"capacity": Gf.Vec3f(1.0, 0.1, 0.1), "inflow": Gf.Vec3f(1.0, 0.1, 0.1),
                 "sensor_outage": Gf.Vec3f(1.0, 0.65, 0.1)}

# density -> colour ramp: green (calm) -> yellow -> orange -> red (dangerous)
RAMP = [(0.0, (0.15, 0.65, 0.30)), (2.0, (0.95, 0.85, 0.20)),
        (3.5, (1.00, 0.50, 0.10)), (5.0, (0.85, 0.10, 0.10)), (MAX_DENSITY, (0.85, 0.10, 0.10))]


def colour_for(density):
    d = max(0.0, min(density, MAX_DENSITY))
    for (d0, c0), (d1, c1) in zip(RAMP, RAMP[1:]):
        if d <= d1:
            t = (d - d0) / (d1 - d0) if d1 > d0 else 0
            return Gf.Vec3f(*[a + (b - a) * t for a, b in zip(c0, c1)])
    return Gf.Vec3f(*RAMP[-1][1])


def box(stage, path, centre, size, colour):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    xf = UsdGeom.XformCommonAPI(cube)
    xf.SetTranslate(Gf.Vec3d(*centre))
    xf.SetScale(Gf.Vec3f(*size))
    cube.CreateDisplayColorAttr([colour])
    return cube


def zone_nodes(venue):
    return [n for n in venue["nodes"] if n["type"] != "venue"]


# ---------------------------------------------------------------- static layer
def build_layout(venue):
    nodes = {n["id"]: n for n in venue["nodes"]}
    stage = Usd.Stage.CreateNew(str(LAYOUT_FILE))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetTimeCodesPerSecond(MINUTES_PER_SECOND)
    stage.SetFramesPerSecond(MINUTES_PER_SECOND)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    world.GetPrim().SetCustomDataByKey("odtcc:venue", venue["venue"])

    box(stage, "/World/Ground", (0, 0, -0.5), (1400, 1400, 1), Gf.Vec3f(0.25, 0.27, 0.30))
    sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
    sun.CreateIntensityAttr(1000)
    UsdGeom.XformCommonAPI(sun).SetRotate(Gf.Vec3f(-45, 0, 30))
    UsdLux.DomeLight.Define(stage, "/World/Sky").CreateIntensityAttr(150)

    UsdGeom.Scope.Define(stage, "/World/Paths")
    for nid, node in nodes.items():
        for dest in node.get("routes", {}):
            a, b = node, nodes[dest]
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            strip = box(stage, f"/World/Paths/{nid}_to_{dest}",
                        ((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2, 0.2),
                        (math.hypot(dx, dy), 12, 0.4), Gf.Vec3f(0.45, 0.45, 0.48))
            UsdGeom.XformCommonAPI(strip).SetRotate(Gf.Vec3f(0, 0, math.degrees(math.atan2(dy, dx))))

    UsdGeom.Scope.Define(stage, "/World/Looks")
    UsdGeom.Scope.Define(stage, "/World/Zones")
    for nid, node in nodes.items():
        zone = UsdGeom.Xform.Define(stage, f"/World/Zones/{nid}")
        prim = zone.GetPrim()
        prim.SetCustomDataByKey("odtcc:label", node["label"])
        prim.SetCustomDataByKey("odtcc:type", node["type"])
        UsdGeom.XformCommonAPI(zone).SetTranslate(Gf.Vec3d(node["x"], node["y"], 0))

        if node["type"] == "venue":
            bowl = UsdGeom.Cylinder.Define(stage, f"/World/Zones/{nid}/Bowl")
            bowl.CreateRadiusAttr(140)
            bowl.CreateHeightAttr(30)
            UsdGeom.XformCommonAPI(bowl).SetTranslate(Gf.Vec3d(0, 0, 15))
            bowl.CreateDisplayColorAttr([Gf.Vec3f(0.75, 0.72, 0.65)])
            # People still inside the bowl, as a disc on top whose height follows the count
            inside = UsdGeom.Cylinder.Define(stage, f"/World/Zones/{nid}/CrowdInside")
            inside.CreateRadiusAttr(110)
            inside.CreateHeightAttr(1.0)
            inside.CreateDisplayColorAttr([Gf.Vec3f(0.25, 0.45, 0.95)])
            xf = UsdGeom.Xformable(inside)
            xf.AddTranslateOp().Set(Gf.Vec3d(0, 0, BOWL_TOP_M))
            xf.AddScaleOp().Set(Gf.Vec3f(1, 1, 0.01))
            continue

        side = math.sqrt(node["area_m2"])
        mat = UsdShade.Material.Define(stage, f"/World/Looks/{nid}")
        shader = UsdShade.Shader.Define(stage, f"/World/Looks/{nid}/Shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.6)
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(colour_for(0))
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")

        pad = box(stage, f"/World/Zones/{nid}/Pad", (0, 0, 1), (side, side, 2), colour_for(0))
        UsdShade.MaterialBindingAPI.Apply(pad.GetPrim()).Bind(mat)

        pillar = UsdGeom.Cylinder.Define(stage, f"/World/Zones/{nid}/CrowdPillar")
        pillar.CreateRadiusAttr(min(side * 0.25, 20))
        pillar.CreateHeightAttr(1.0)
        UsdShade.MaterialBindingAPI.Apply(pillar.GetPrim()).Bind(mat)
        xf = UsdGeom.Xformable(pillar)
        xf.AddTranslateOp().Set(Gf.Vec3d(0, 0, 2))
        xf.AddScaleOp().Set(Gf.Vec3f(1, 1, 0.01))

        beacon = UsdGeom.Sphere.Define(stage, f"/World/Zones/{nid}/IncidentBeacon")
        beacon.CreateRadiusAttr(12)
        beacon.CreateDisplayColorAttr([BEACON_COLOUR["capacity"]])
        UsdGeom.XformCommonAPI(beacon).SetTranslate(Gf.Vec3d(0, 0, 90))
        beacon.CreateVisibilityAttr(UsdGeom.Tokens.invisible)

        if "train_interval_min" in node:
            train = box(stage, f"/World/Zones/{nid}/Train", (side / 2 + 25, 0, 6), (20, side * 0.9, 10),
                        Gf.Vec3f(0.85, 0.85, 0.90))
            train.CreateVisibilityAttr(UsdGeom.Tokens.invisible)

        prim.CreateAttribute("odtcc:density", Sdf.ValueTypeNames.Float).Set(0.0)
        prim.CreateAttribute("odtcc:occupancy", Sdf.ValueTypeNames.Int).Set(0)
        prim.CreateAttribute("odtcc:level", Sdf.ValueTypeNames.String).Set("normal")

    cam = UsdGeom.Camera.Define(stage, "/World/OverviewCam")
    cam.CreateFocalLengthAttr(24)
    cam.CreateClippingRangeAttr(Gf.Vec2f(1, 10000))
    UsdGeom.XformCommonAPI(cam).SetTranslate(Gf.Vec3d(250, -1100, 750))
    UsdGeom.XformCommonAPI(cam).SetRotate(Gf.Vec3f(55, 0, 10))

    stage.GetRootLayer().Save()


# ---------------------------------------------------------------- crowd layer
def load_sim(path):
    series = defaultdict(dict)
    with open(path) as f:
        for row in csv.DictReader(f):
            series[row["node"]][int(row["minute"])] = row
    return series


def _sim_from_df(df):
    """Convert a simulator DataFrame to {node: {minute: row_dict}}."""
    series = defaultdict(dict)
    for row in df.itertuples(index=False):
        series[row.node][int(row.minute)] = {
            "density": row.density,
            "occupancy": row.occupancy,
            "level": row.level,
            "outflow": row.outflow,
        }
    return dict(series)


def _write_crowd_to_stage(stage, crowd, sim, venue):
    """Write time-varying crowd data into the crowd layer via stage (gets prim paths from it)."""
    stage.SetEditTarget(Usd.EditTarget(crowd))
    for node in zone_nodes(venue):
        nid = node["id"]
        zone = stage.GetPrimAtPath(f"/World/Zones/{nid}")
        pad = UsdGeom.Gprim(stage.GetPrimAtPath(f"/World/Zones/{nid}/Pad"))
        diffuse = UsdShade.Shader(stage.GetPrimAtPath(f"/World/Looks/{nid}/Shader")).GetInput("diffuseColor")
        p_translate, p_scale = UsdGeom.Xformable(
            stage.GetPrimAtPath(f"/World/Zones/{nid}/CrowdPillar")).GetOrderedXformOps()
        density_attr = zone.GetAttribute("odtcc:density")
        occ_attr = zone.GetAttribute("odtcc:occupancy")
        level_attr = zone.GetAttribute("odtcc:level")

        for minute, row in sorted(sim[nid].items()):
            density = float(row["density"] or 0)
            h = max(density * PILLAR_M_PER_DENSITY, 0.01)
            c = colour_for(density)
            diffuse.Set(c, minute)
            pad.GetDisplayColorAttr().Set([c], minute)
            p_translate.Set(Gf.Vec3d(0, 0, 2 + h / 2), minute)
            p_scale.Set(Gf.Vec3f(1, 1, h), minute)
            density_attr.Set(density, minute)
            occ_attr.Set(int(row["occupancy"]), minute)
            level_attr.Set(str(row["level"]), minute)

    stadium = next(n for n in venue["nodes"] if n["type"] == "venue")
    t_op, s_op = UsdGeom.Xformable(
        stage.GetPrimAtPath(f"/World/Zones/{stadium['id']}/CrowdInside")).GetOrderedXformOps()
    minutes = sorted(sim[stadium["id"]])
    for minute in minutes:
        h = max(float(sim[stadium["id"]][minute]["occupancy"]) / venue["attendance"] * BOWL_FILL_M, 0.01)
        t_op.Set(Gf.Vec3d(0, 0, BOWL_TOP_M + h / 2), minute)
        s_op.Set(Gf.Vec3f(1, 1, h), minute)

    for node in venue["nodes"]:
        if "train_interval_min" in node:  # the train is in the station on the minutes it departs
            vis = UsdGeom.Imageable(stage.GetPrimAtPath(f"/World/Zones/{node['id']}/Train")).GetVisibilityAttr()
            for minute, row in sorted(sim[node["id"]].items()):
                departs = float(row["outflow"] or 0) > 0
                vis.Set(UsdGeom.Tokens.inherited if departs else UsdGeom.Tokens.invisible, minute)

    for inc in venue.get("incidents", []):  # pulsing beacon over the zone while the incident lasts
        beacon = stage.GetPrimAtPath(f"/World/Zones/{inc['node']}/IncidentBeacon")
        UsdGeom.Gprim(beacon).GetDisplayColorAttr().Set([BEACON_COLOUR[inc["type"]]])
        vis, xf = UsdGeom.Imageable(beacon).GetVisibilityAttr(), UsdGeom.XformCommonAPI(beacon)
        for minute in minutes:
            active = inc["from"] <= minute < inc["to"]
            vis.Set(UsdGeom.Tokens.inherited if active else UsdGeom.Tokens.invisible, minute)
            xf.SetScale(Gf.Vec3f(1.6 if minute % 2 else 1.0), minute)


def build_scenario(venue, scenario=None, df=None, crowd_file=None, twin_file=None):
    """Build crowd layer and twin USD.

    Pass scenario (a name string) to read from egress_<scenario>.csv with default output paths.
    Pass df (a simulator DataFrame) with explicit crowd_file and twin_file paths.
    """
    if df is not None:
        sim = _sim_from_df(df)
        minutes = int(df["minute"].max()) + 1
    else:
        sim = load_sim(egress_csv(scenario))
        minutes = max(max(m) for m in sim.values()) + 1
    crowd_file = Path(crowd_file) if crowd_file else LAYER_DIR / f"crowd_{scenario}.usda"
    twin_file = Path(twin_file) if twin_file else USD_DIR / f"twin_{scenario}.usda"

    # The crowd layer holds only animated overrides on top of the layout
    crowd = Sdf.Layer.CreateNew(str(crowd_file))
    # Every layer must share the same time rate, or USD rescales samples between layers
    crowd.timeCodesPerSecond = MINUTES_PER_SECOND
    crowd.framesPerSecond = MINUTES_PER_SECOND
    crowd.Save()
    twin = Usd.Stage.CreateNew(str(twin_file))
    root = twin.GetRootLayer()
    # Sublayer paths are relative to the twin file, so the project folder can move
    root.subLayerPaths.append(os.path.relpath(crowd_file, USD_DIR))   # stronger: crowd data
    root.subLayerPaths.append(os.path.relpath(LAYOUT_FILE, USD_DIR))  # weaker: static venue
    UsdGeom.SetStageUpAxis(twin, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(twin, 1.0)
    twin.SetStartTimeCode(0)
    twin.SetEndTimeCode(minutes - 1)
    twin.SetTimeCodesPerSecond(MINUTES_PER_SECOND)
    twin.SetFramesPerSecond(MINUTES_PER_SECOND)
    twin.SetDefaultPrim(twin.GetPrimAtPath("/World"))
    twin.GetPrimAtPath("/World").SetCustomDataByKey("odtcc:scenario", scenario or "live")

    _write_crowd_to_stage(twin, crowd, sim, venue)

    crowd.Save()
    root.Save()
    return twin_file, minutes


def build_live_twin(venue, baseline_df):
    """Create twin_live.usda + crowd_live.usda seeded with baseline data. Called at server startup."""
    for f in [CROWD_LIVE_FILE, TWIN_LIVE_FILE]:
        if os.path.exists(f):
            os.remove(f)
    os.makedirs(LAYER_DIR, exist_ok=True)
    os.makedirs(USD_DIR, exist_ok=True)
    build_scenario(venue, df=baseline_df, crowd_file=CROWD_LIVE_FILE, twin_file=TWIN_LIVE_FILE)


def build_crowd_layer(venue, df, crowd_file=None):
    """Rebuild the crowd layer from a DataFrame without touching the twin file.

    Used by /approve. After this returns, Kit calls Sdf.Layer.Find(...).Reload()
    to pick up the new data without reopening the stage.
    """
    crowd_file = Path(crowd_file) if crowd_file else CROWD_LIVE_FILE
    if os.path.exists(crowd_file):
        os.remove(crowd_file)

    sim = _sim_from_df(df)
    minutes = int(df["minute"].max()) + 1

    crowd = Sdf.Layer.CreateNew(str(crowd_file))
    crowd.timeCodesPerSecond = MINUTES_PER_SECOND
    crowd.framesPerSecond = MINUTES_PER_SECOND
    crowd.Save()

    # In-memory stage (no file) — used only to resolve prim paths; never saved.
    # Absolute paths are required because there is no real file to resolve relative paths against.
    stage = Usd.Stage.CreateInMemory()
    stage.GetRootLayer().subLayerPaths = [str(crowd_file), str(LAYOUT_FILE)]
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    stage.SetTimeCodesPerSecond(MINUTES_PER_SECOND)
    stage.SetFramesPerSecond(MINUTES_PER_SECOND)
    stage.SetEndTimeCode(minutes - 1)

    _write_crowd_to_stage(stage, crowd, sim, venue)
    crowd.Save()


def main():
    venue = json.load(open(VENUE_FILE))
    os.makedirs(LAYER_DIR, exist_ok=True)
    live = {str(CROWD_LIVE_FILE), str(TWIN_LIVE_FILE)}  # owned by the server
    for f in [LAYOUT_FILE] + glob.glob(f"{LAYER_DIR}/crowd_*.usda") + glob.glob(f"{USD_DIR}/twin_*.usda"):
        if os.path.exists(f) and str(f) not in live:
            os.remove(f)  # rebuild cleanly; CreateNew refuses to overwrite existing layers

    scenarios = sys.argv[1:] or sorted(
        os.path.basename(p)[len("egress_"):-len(".csv")] for p in glob.glob(f"{DATA_DIR}/egress_*.csv"))
    build_layout(venue)
    print(f"Wrote {LAYOUT_FILE}")
    for s in scenarios:
        twin_file, minutes = build_scenario(venue, scenario=s)
        print(f"Wrote {os.path.relpath(twin_file)}  (scenario '{s}', {minutes} min = {minutes / MINUTES_PER_SECOND:.0f} s playback)")


if __name__ == "__main__":
    main()