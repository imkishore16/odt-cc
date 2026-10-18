"""Thin HTTP client for the brain service. Blocking urllib calls run in an executor thread
so Kit's UI never stalls. Mock mode answers from the JSON files in mock/."""
import asyncio
import copy
import json
import urllib.request
from pathlib import Path

MOCK_DIR = Path(__file__).resolve().parents[2] / "mock"


class BrainClient:
    def __init__(self, url, mock=False):
        self.url = url.rstrip("/")
        self.mock = mock
        self._mock_approved = False

    async def state(self, minute):
        return await self._run("GET", f"/state?minute={minute}", timeout=5)

    async def recommend(self, minute):
        return await self._run("POST", "/recommend", {"minute": minute}, timeout=90)

    async def approve(self, plan, minute):
        return await self._run("POST", "/approve", {"plan": plan, "minute": minute}, timeout=30)

    async def reset(self):
        return await self._run("POST", "/reset", {}, timeout=30)

    async def scenarios(self):
        return await self._run("GET", "/scenarios", timeout=5)

    async def set_scenario(self, scenario_id, incidents=None, planning=None):
        return await self._run("POST", "/scenario", {"id": scenario_id, "incidents": incidents, "planning": planning},
                               timeout=30)

    async def inject(self, minute, kind=None):
        return await self._run("POST", "/incident/inject", {"minute": minute, "kind": kind}, timeout=30)

    async def compare(self, plans, minute, planning):
        body = {"plans": [{"name": n, "plan": p} for n, p in plans.items()], "minute": minute, "planning": planning}
        return await self._run("POST", "/compare", body, timeout=60)

    async def keep(self, minute):
        return await self._run("POST", "/decision/keep", {"minute": minute}, timeout=5)

    async def card(self):
        return await self._run("GET", "/card", timeout=5)

    async def levers(self):
        return await self._run("GET", "/levers", timeout=5)

    async def simulate(self, plan, minute):
        return await self._run("POST", "/simulate", {"plan": plan, "minute": minute}, timeout=30)

    async def _run(self, method, path, body=None, timeout=10):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._call, method, path, body, timeout)

    def _call(self, method, path, body, timeout):
        if self.mock:
            return self._mock(path, body)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _mock(self, path, body):
        if path.startswith("/state"):
            minute = int(path.split("=")[1])
            st = json.loads((MOCK_DIR / "state.json").read_text())
            st["minute"] = minute
            for z in st["zones"]:
                z["predicted_minute"] = minute + 15
            if minute < 2 or self._mock_approved:  # the mock alert fires at minute 2, like the real one
                for z in st["zones"]:
                    z["alert_band"] = "ok"
            st["alerts"] = [z for z in st["zones"] if z["alert_band"] != "ok"]
            return st
        if path == "/recommend":
            card = json.loads((MOCK_DIR / "recommend.json").read_text())
            card["minute"] = body["minute"]
            return card
        if path == "/approve":
            self._mock_approved = True
            res = json.loads((MOCK_DIR / "approve.json").read_text())
            res.update(minute=body["minute"], plan=copy.deepcopy(body["plan"]))
            return res
        if path == "/reset":
            self._mock_approved = False
            return {"status": "reset"}
        if path == "/scenarios":
            return json.loads((MOCK_DIR / "scenarios.json").read_text())
        if path == "/scenario":
            self._mock_approved = False
            return {"status": "ok", "scenario": body["id"], "description": f"Mock: {body.get('incidents')}"}
        if path == "/levers":
            return json.loads((MOCK_DIR / "levers.json").read_text())
        if path == "/simulate":
            res = json.loads((MOCK_DIR / "simulate.json").read_text())
            res.update(minute=body["minute"], plan=copy.deepcopy(body["plan"]))
            return res
        raise ValueError(f"no mock for {path}")
