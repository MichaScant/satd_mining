#!/usr/bin/env bash
# Clean the agent introduction channel of tool/summary posts, recompute circumstance + outcome, and
# print the spotter hierarchy (who spotted -> how agent spotted -> revisits to resolution).
#
#   bash run_spotter_rebuild.sh        # all local, no token needed
set -uo pipefail
cd "$(dirname "$0")"
LOG="run_spotter_rebuild.log"; : > "$LOG"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
run() { echo "" | tee -a "$LOG"; echo "===== [$(ts)] $1 =====" | tee -a "$LOG"; shift
        python3 -u "$@" 2>&1 | tee -a "$LOG"; }

run "reclassify (drop tool/summary posts from agent channel)" reclassify_tool_posts.py
run "analyze (recompute circumstance on the clean agent set)" analyze_reviewer_elicited_satd.py
run "stage6 (recompute per-SATD outcome)"                     stage6_resolution_outcome.py
run "spotter hierarchy (Level 1/2/3)"                         analyze_spotter_hierarchy.py
echo "" | tee -a "$LOG"; echo "[$(ts)] DONE -> $LOG" | tee -a "$LOG"
