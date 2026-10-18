"""
ODT-CC — Phase 0b smoke test for Nemotron 3.5 Lightning (local NIM or hosted endpoint).

Runs three checks and prints latency for each:
  1. fast chat answer (thinking off)
  2. tool calling: the model must call get_zone_forecast for the right zone
  3. full tool round-trip: feed the tool result back, get the operator briefing

Usage:
  python scripts/nim_smoke_test.py            # local NIM at http://localhost:8001/v1
  python scripts/nim_smoke_test.py --hosted   # https://integrate.api.nvidia.com/v1 (needs NVIDIA_API_KEY)
Needs: pip install openai
"""
import argparse
import json
import os
import sys
import time

from openai import OpenAI

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
FAST = {"chat_template_kwargs": {"enable_thinking": False}}

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_zone_forecast",
        "description": "Predicted crowd density (people/m²) for a venue zone 15 minutes from now. "
                       "Density >= 5 is dangerous.",
        "parameters": {
            "type": "object",
            "properties": {
                "zone": {"type": "string",
                         "enum": ["walkway_station", "station_platform", "gate_N", "shuttle_stop", "plaza_S"],
                         "description": "Zone id"},
            },
            "required": ["zone"],
        },
    },
}]
SYSTEM = ("You are ODT-CC, a crowd-safety assistant for LA28 venue operators. "
          "Use tools for any numbers. Be brief. Never act without operator approval.")


def timed(label, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = time.perf_counter() - t0
    print(f"  [{label}] {dt:.2f}s")
    return out, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosted", action="store_true")
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args()

    if args.hosted:
        key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NGC_API_KEY")
        if not key:
            sys.exit("Set NVIDIA_API_KEY (the nvapi-... key from build.nvidia.com)")
        base = args.base_url or "https://integrate.api.nvidia.com/v1"
    else:
        key = "not-needed"
        base = args.base_url or "http://localhost:8001/v1"
    client = OpenAI(base_url=base, api_key=key)
    print(f"Endpoint: {base}\nModel:    {MODEL}\n")
    results = {}

    # 1. plain fast answer
    print("1) Fast chat (thinking off)")
    r, dt = timed("chat", lambda: client.chat.completions.create(
        model=MODEL, max_tokens=120, temperature=0.2, extra_body=FAST,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": "In one sentence: why is 6 people per m² dangerous in a crowd?"}]))
    print("  ->", r.choices[0].message.content.strip()[:300])
    results["chat_s"] = round(dt, 2)
    if r.usage:
        print(f"  tokens: {r.usage.prompt_tokens} in / {r.usage.completion_tokens} out")

    # 2. tool call
    print("\n2) Tool calling")
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Is the walkway to the Expo Park station going to be safe in 15 minutes?"}]
    r, dt = timed("tool-call", lambda: client.chat.completions.create(
        model=MODEL, max_tokens=300, temperature=0.0, extra_body=FAST,
        messages=messages, tools=TOOLS, tool_choice="auto"))
    msg = r.choices[0].message
    calls = msg.tool_calls or []
    results["tool_call_s"] = round(dt, 2)
    if not calls:
        print("  FAIL: no tool call. Model said:", (msg.content or "")[:300])
        results["tool_call_ok"] = False
    else:
        c = calls[0]
        print(f"  -> {c.function.name}({c.function.arguments})")
        ok = c.function.name == "get_zone_forecast" and json.loads(c.function.arguments).get("zone") == "walkway_station"
        print("  PASS" if ok else "  WARN: unexpected tool/arguments")
        results["tool_call_ok"] = ok

        # 3. round-trip with a fake forecast result
        print("\n3) Tool result -> operator briefing")
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments}}]})
        messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(
            {"zone": "walkway_station", "density_now": 3.0, "predicted_density_in_15_min": 6.4,
             "level": "dangerous"})})
        r, dt = timed("briefing", lambda: client.chat.completions.create(
            model=MODEL, max_tokens=200, temperature=0.2, extra_body=FAST, messages=messages, tools=TOOLS))
        print("  ->", (r.choices[0].message.content or "").strip()[:500])
        results["briefing_s"] = round(dt, 2)

    print("\nSummary:", json.dumps(results))


if __name__ == "__main__":
    main()