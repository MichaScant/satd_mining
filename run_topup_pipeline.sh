#!/usr/bin/env bash
# Top-up pipeline: expand the AIDev SATD set to merged PRs only, then rebuild every downstream
# channel (conversation, reviewer, resolution, revisits, issue) on the expanded data.
#
#   export GITHUB_TOKEN=<classic-PAT>      # needed for the two fetch stages (else they warn+skip)
#   PER_AGENT=2500 bash run_topup_pipeline.sh
#
# Range increase: stage3_aidev --merged-only scans deeper into each agent's merged pool until it
# finds PER_AGENT code SATD (or exhausts). jules is excluded (already exhausted); copilot excluded.
# Everything after the scan is incremental/idempotent (stage5 processes only status-NULL instances;
# upserts dedupe; stage3e is resumable), so re-running is safe.
#
# Long pole = stage5_aidev (clones each new instance's repo) and codex's low SATD density. Run in
# byobu; detach with F6.
set -uo pipefail
cd "$(dirname "$0")"

PER_AGENT="${PER_AGENT:-2500}"
EXCLUDE="${EXCLUDE:-copilot,jules}"
LOG="run_topup_pipeline.log"; : > "$LOG"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
declare -A STATUS
ORDER=()

run_stage() {                       # run_stage <abort|warn> <name> <cmd...>
  local mode="$1"; local name="$2"; shift 2
  ORDER+=("$name")
  echo "" | tee -a "$LOG"
  echo "===== [$(ts)] START $name =====" | tee -a "$LOG"
  local start; start=$(date +%s)
  if python3 -u "$@" 2>&1 | tee -a "$LOG"; then
    STATUS[$name]=OK
  else
    STATUS[$name]="FAILED(${PIPESTATUS[0]})"
  fi
  echo "===== [$(ts)] END $name — ${STATUS[$name]} ($(( $(date +%s)-start ))s) =====" | tee -a "$LOG"
  if [[ "${STATUS[$name]}" != OK && "$mode" == abort ]]; then
    echo "[$(ts)] ABORT: $name failed and later steps depend on it." | tee -a "$LOG"
    summary; exit 1
  fi
}

summary() {
  echo "" | tee -a "$LOG"; echo "===== [$(ts)] SUMMARY =====" | tee -a "$LOG"
  for s in "${ORDER[@]}"; do printf '  %-16s %s\n' "$s" "${STATUS[$s]:-SKIPPED}" | tee -a "$LOG"; done
  echo "full log: $(pwd)/$LOG" | tee -a "$LOG"
}

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "[$(ts)] WARNING: GITHUB_TOKEN unset — commit-date + issue fetch stages will warn+skip." | tee -a "$LOG"
fi

echo "[$(ts)] top-up pipeline start (PER_AGENT=$PER_AGENT, exclude=$EXCLUDE)" | tee -a "$LOG"

# --- 1. expand the code-SATD set to MERGED PRs only ---------------------------------------------
run_stage abort scan stage3_aidev.py --aidev-dir data/aidev --exclude "$EXCLUDE" \
                      --merged-only --per-agent "$PER_AGENT" --record-sample

# --- 2. conversation + reviewer channels over the (now larger) in-scope sample ------------------
run_stage abort stage3b  stage3b_conversational.py
run_stage abort stage3c  stage3c_reviewer_flags.py

# --- 3. resolution: code (clones), then conversation ---------------------------------------------
run_stage abort stage5   stage5_aidev.py
run_stage abort stage5b  stage5b_conversation_resolution.py --cohort aidev

# --- 4. commit dates (git) BEFORE revisits so pre-resolution ordering is current -----------------
run_stage warn  dates    fetch_pr_commit_dates.py
run_stage warn  stage5c  stage5c_aidev_revisits.py
run_stage warn  stage5d  stage5d_pre_resolution_dialogue.py --cohort aidev

# --- 5. issue channel on the new PRs (detect -> fetch -> join) -----------------------------------
run_stage warn  stage3d  stage3d_issue_refs.py --cohort aidev
run_stage warn  stage3e  stage3e_issue_resolution.py
run_stage warn  stage5f  stage5f_issue_join.py

# --- 6. analysis + export ------------------------------------------------------------------------
run_stage warn  elicited analyze_reviewer_elicited_satd.py
run_stage warn  export   export_satd_conversations.py

summary
echo "[$(ts)] DONE. Pull db/satd_mining.db + agent_notice_revisit.csv to the Mac and regenerate graphs." | tee -a "$LOG"
