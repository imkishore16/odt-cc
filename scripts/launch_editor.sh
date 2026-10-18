#!/usr/bin/env bash
# ODT-CC — launch Kit Base Editor on the Launchpad desktop, pinned to GPU 0
# (GPU 1 is kept free for the Nemotron NIM).
#
# Usage:  scripts/launch_editor.sh                      open usd/twin_live.usda (start the server first; it creates it)
#         scripts/launch_editor.sh usd/twin_plan.usda   open another stage (path from project root)
# Logs:   ~/editor.log
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
KIT_APP="$HOME/Desktop/kit-app-template/_build/linux-x86_64/release/my_company.my_editor.kit.sh"
STAGE="$PROJECT/${1:-usd/twin_live.usda}"

ARGS=(
  --/renderer/multiGpu/enabled=false
  --/renderer/multiGpu/autoEnable=false
  --/renderer/multiGpu/maxGpuCount=1
  --/renderer/activeGpu=0
)
# Load our command-center extension once it exists (Phase 4)
if [ -d "$PROJECT/kit/exts" ]; then
  ARGS+=(--ext-folder "$PROJECT/kit/exts")
  [ -d "$PROJECT/kit/exts/odtcc.command_center" ] && ARGS+=(--enable odtcc.command_center)
fi
# The editor ignores a positional stage path; our extension opens this one once the app is ready
ARGS+=("--/exts/odtcc.command_center/open_stage=$STAGE")

export DISPLAY="${DISPLAY:-:1}"
nohup "$KIT_APP" "${ARGS[@]}" > "$HOME/editor.log" 2>&1 &
echo "Kit editor starting (pid $!) on GPU 0 with $STAGE — log: ~/editor.log"
