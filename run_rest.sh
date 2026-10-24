#!/usr/bin/env bash
# Run the post-fetch stages in sequence — use this AFTER you've cancelled stage2
# (e.g. once ~6000 PRs are fetched). Order:
#   3a  stage3_satd_scan.py        Channel A: code-comment SATD over fetched patches
#   3c  stage3c_reviewer_flags.py  reviewer flags + first-pointer determination
#   5   stage5_track.py            clone repos, pickaxe, resolution + survival
#
# Stage 3b (conversational) is intentionally NOT here — it runs on stage1 data, not on
# what stage2 fetched, and you already ran it. Uncomment its line below if it never finished.
#
#   bash run_rest.sh                 # inherits $GITHUB_TOKEN if exported
#   GITHUB_TOKEN=ghp_... bash run_rest.sh
#   nohup bash run_rest.sh &         # survive disconnect; follow with: tail -f run_rest.log
#
# Keeps going if a stage errors and prints a pass/fail summary at the end. Everything
# (with timestamps) is tee'd to run_rest.log.
set -uo pipefail
cd "$(dirname "$0")"

LOG="run_rest.log"
: > "$LOG"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
declare -A STATUS

run_stage() {
  local name="$1"; shift
  echo ""                                        | tee -a "$LOG"
  echo "===== [$(ts)] START $name ====="         | tee -a "$LOG"
  local start; start=$(date +%s)
  if "$@" 2>&1 | tee -a "$LOG"; then
    STATUS[$name]="OK"
  else
    STATUS[$name]="FAILED (exit ${PIPESTATUS[0]})"
  fi
  local dur=$(( $(date +%s) - start ))
  echo "===== [$(ts)] END $name — ${STATUS[$name]} (${dur}s) =====" | tee -a "$LOG"
}

echo "[$(ts)] pipeline start. GITHUB_TOKEN $([ -n "${GITHUB_TOKEN:-}" ] && echo set || echo UNSET)" | tee -a "$LOG"

# run_stage "stage3b_conversational" python3 stage3b_conversational.py   # only if it never finished
run_stage "stage3_satd_scan"       python3 stage3_satd_scan.py
run_stage "stage3c_reviewer_flags" python3 stage3c_reviewer_flags.py

# disk check before stage5 clones repos (you've hit the quota wall before)
echo "[$(ts)] disk before stage5:" | tee -a "$LOG"
df -h . | tee -a "$LOG"
du -sh data/clones 2>/dev/null | tee -a "$LOG" || true

run_stage "stage5_track"           python3 stage5_track.py
# stamp agent attribution + fill revisit counts on already-tracked instances (from events)
run_stage "backfill_attribution"   python3 backfill_attribution.py
# text report + CSV (graphs too if matplotlib is installed on this box)
run_stage "report"                 python3 report.py

echo ""                                 | tee -a "$LOG"
echo "===== [$(ts)] SUMMARY ====="       | tee -a "$LOG"
for s in stage3_satd_scan stage3c_reviewer_flags stage5_track backfill_attribution report; do
  printf '  %-24s %s\n' "$s" "${STATUS[$s]:-SKIPPED}" | tee -a "$LOG"
done
echo "full log: $(pwd)/$LOG" | tee -a "$LOG"
