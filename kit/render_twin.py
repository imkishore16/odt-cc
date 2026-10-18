"""
ODT-CC — render the OpenUSD twin headless (no window) with Omniverse RTX.

Runs INSIDE the Kit app (not with plain python). It opens a twin, points the
viewport at /World/OverviewCam, steps the timeline one simulated minute at a time,
and saves a PNG per minute. Stitch the PNGs into an MP4 with ffmpeg afterwards.

Run (from ~/Desktop/kit-app-template):
  ODTCC_STAGE=/home/nvidia/Desktop/Olympic_DigitalTwin/Nvidia/usd/twin_baseline.usda \
  ODTCC_OUT=/home/nvidia/Desktop/Olympic_DigitalTwin/Nvidia/renders/baseline \
  ./_build/linux-x86_64/release/my_company.my_usd_composer.kit.sh \
      --no-window --exec /home/nvidia/Desktop/Olympic_DigitalTwin/Nvidia/kit/render_twin.py

Optional env vars: ODTCC_STEP (minutes per frame, default 1), ODTCC_WIDTH/HEIGHT (1280x720),
ODTCC_SETTLE (render updates per frame, default 30 — raise if frames look noisy).
"""
import asyncio
import os

import carb
import omni.kit.app
import omni.timeline
import omni.usd
from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport

STAGE = os.environ["ODTCC_STAGE"]
OUT = os.environ.get("ODTCC_OUT", os.path.join(os.path.dirname(STAGE), "..", "renders"))
STEP = int(os.environ.get("ODTCC_STEP", "1"))
WIDTH = int(os.environ.get("ODTCC_WIDTH", "1280"))
HEIGHT = int(os.environ.get("ODTCC_HEIGHT", "720"))
SETTLE = int(os.environ.get("ODTCC_SETTLE", "30"))
CAMERA = "/World/OverviewCam"


def log(msg):
    carb.log_warn(f"[odtcc] {msg}")  # warn level so it shows in the terminal
    print(f"[odtcc] {msg}", flush=True)


async def wait_frames(n):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


async def render():
    app = omni.kit.app.get_app()
    try:
        os.makedirs(OUT, exist_ok=True)
        ctx = omni.usd.get_context()
        log(f"opening {STAGE}")
        await ctx.open_stage_async(STAGE)
        stage = ctx.get_stage()
        if stage is None:
            raise RuntimeError("stage failed to open")

        await wait_frames(60)  # let RTX finish loading shaders/textures
        viewport = get_active_viewport()
        if viewport is None:
            raise RuntimeError("no viewport available in this app")
        viewport.resolution = (WIDTH, HEIGHT)
        viewport.camera_path = CAMERA

        start = int(stage.GetStartTimeCode())
        end = int(stage.GetEndTimeCode())
        tcps = stage.GetTimeCodesPerSecond()
        timeline = omni.timeline.get_timeline_interface()
        timeline.stop()

        minutes = list(range(start, end + 1, STEP))
        log(f"rendering {len(minutes)} frames at {WIDTH}x{HEIGHT} to {OUT}")
        for i, minute in enumerate(minutes):
            timeline.set_current_time(minute / tcps)
            await wait_frames(SETTLE)
            path = os.path.join(OUT, f"frame_{minute:03d}.png")
            capture = capture_viewport_to_file(viewport, path)
            await capture.wait_for_result()
            if i % 10 == 0:
                log(f"  minute {minute} -> {path}")
        log("done")
    except Exception as e:  # make failures visible instead of hanging
        log(f"ERROR: {e!r}")
    finally:
        app.post_quit()


asyncio.ensure_future(render())
