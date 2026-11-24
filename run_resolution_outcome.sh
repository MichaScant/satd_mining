#!/usr/bin/env bash
# Build the unified per-SATD resolution OUTCOME on the current cohort (no top-up scan), end to end.
#
#   export GITHUB_TOKEN=<classic-PAT>      # only used if stage3e has NEW issues to fetch (already
#                                          # cached from the earlier run, so normally a no-op)
#   bash run_resolution_outcome.sh
#
# Order: rebuild issue candidate links -> (re)fetch issue state [resumable] -> fold issues into
# status -> classify every declared SATD as resolved/dropped/untrackable. All idempotent; safe to
# re-run. stage3d uses INSERT OR IGNORE so the already-fetched issue state is preserved.
set -uo pipefail
cd "$(dirname "$0")"

LOG="run_resolution_outcome.log"; : > "$LOG"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
declare -A STATUS; ORDER=()

run_stage() {                       # run_stage <abort|warn> <name> <cmd...>
  local mode="$1"; local name="$2"; shift 2
  ORDER+=("$name")
  echo "" | tee -a "$LOG"; echo "===== [$(ts)] START $name =====" | tee -a "$LOG"
  local start; start=$(date +%s)
  if python3 -u "$@" 2>&1 | tee -a "$LOG"; then STATUS[$name]=OK
  else STATUS[$name]="FAILED(${PIPESTATUS[0]})"; fi
  echo "===== [$(ts)] END $name — ${STATUS[$name]} ($(( $(date +%s)-start ))s) =====" | tee -a "$LOG"
  if [[ "${STATUS[$name]}" != OK && "$mode" == abort ]]; then
    echo "[$(ts)] ABORT: $name failed." | tee -a "$LOG"; summary; exit 1
  fi
}
summary() {
  echo "" | tee -a "$LOG"; echo "===== [$(ts)] SUMMARY =====" | tee -a "$LOG"
  for s in "${ORDER[@]}"; do printf '  %-10s %s\n' "$s" "${STATUS[$s]:-SKIPPED}" | tee -a "$LOG"; done
  echo "full log: $(pwd)/$LOG" | tee -a "$LOG"
}

[[ -z "${GITHUB_TOKEN:-}" ]] && echo "[$(ts)] note: GITHUB_TOKEN unset — stage3e will no-op if nothing new to fetch." | tee -a "$LOG"
echo "[$(ts)] resolution-outcome pipeline start" | tee -a "$LOG"

run_stage abort stage3d  stage3d_issue_refs.py --cohort aidev   # rebuild candidate links (dedup; keeps fetched state)
run_stage warn  stage3e  stage3e_issue_resolution.py            # resumable GitHub fetch (cached -> 0 new)
run_stage warn  stage3f  stage3f_issue_closing_pr.py            # GitHub: closing PR + closed-by-merge + revisits
run_stage warn  stage5f  stage5f_issue_join.py                  # valid+closed issue -> resolved_via_issue
run_stage abort stage6   stage6_resolution_outcome.py           # THE deliverable: per-SATD outcome + tables

summary
echo "[$(ts)] DONE. Pull db/satd_mining.db to the Mac for graphs." | tee -a "$LOG"
