#!/usr/bin/env bash
# Capture the FULL agent<->developer discussion (not just the 31% in the code-scan sample), then
# recompute circumstance/outcome and the spotter hierarchy. stage3b now scans every PR's discussion
# (AIDEV_CONV_SCOPE=full), so the well-classified discussion channel gets its true weight and the
# spontaneous share falls toward the real ~33% instead of being dominated by pr_body/first-commit code.
#
#   bash run_full_discussion.sh        # all local, no token
set -uo pipefail
cd "$(dirname "$0")"
export AIDEV_CONV_SCOPE=full
LOG="run_full_discussion.log"; : > "$LOG"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
run() { echo "" | tee -a "$LOG"; echo "===== [$(ts)] $1 =====" | tee -a "$LOG"; shift
        python3 -u "$@" 2>&1 | tee -a "$LOG"; }

run "stage3b — scan ALL discussions (adds the missing ~69%)" stage3b_conversational.py
run "stage3c — reviewer flags (full scope)"                  stage3c_reviewer_flags.py
run "stage5b — resolve conversation SATD"                    stage5b_conversation_resolution.py --cohort aidev
run "analyze — recompute circumstance (full discussion)"     analyze_reviewer_elicited_satd.py
run "stage6 — per-SATD outcome"                              stage6_resolution_outcome.py
run "spotter hierarchy (Level 1/2/3)"                        analyze_spotter_hierarchy.py
echo "" | tee -a "$LOG"; echo "[$(ts)] DONE -> $LOG" | tee -a "$LOG"
