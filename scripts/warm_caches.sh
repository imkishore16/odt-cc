#!/usr/bin/env bash
# ODT-CC — before the demo: save the optimizer result and a Nemotron card for each scenario at the minute
# its run pauses, so ODTCC_LLM=cached (offline fallback) has a card to replay. Needs the NIM running.
set -euo pipefail
cd "$(dirname "$0")/.."

# scenario:minute — the minute each scenario auto-pauses (first alert from the sensor feeds)
for pair in normal:3 expo_line_delay:2 bmo_event:2 sensor_outage:4; do
  scenario=${pair%%:*}; minute=${pair##*:}
  echo "== $scenario, minute $minute"
  .venv/bin/python -m odtcc.optimizer --minute "$minute" --scenario "$scenario" | head -1
  .venv/bin/python -m odtcc.agent --minute "$minute" --scenario "$scenario" | grep -E "action card|Recommended:"
done
