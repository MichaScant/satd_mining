#!/usr/bin/env bash
# Run the AIDev conversation pipeline end-to-end, in order, with logging.
#
#   bash run_conversation_pipeline.sh
#
# Steps (each depends on the previous, so a core failure aborts):
#   1. reset_aidev_review_classification.py  -> 3-way actor types in place + clear aidev reviewer_flags
#   2. stage3b_conversational.py             -> conversation SATD (agent_reply / ai_reviewer_flag / developer_request / pr_body)
#   3. stage3c_reviewer_flags.py             -> reviewer flags + who-pointed-first (self / ai_reviewer / human)
#   4. stage5b_conversation_resolution.py    -> conversation resolution (fix / revisit / alive / disputed)
#   5. verify_conv_pipeline.py               -> sanity checks (non-fatal)
#   6. export_satd_conversations.py          -> manual-analysis CSV (non-fatal)
#
# All local (parquet + SQLite); no GitHub/GPU. Prereqs already done: stage2_aidev --what reviews
# (review_events loaded) and stage3_aidev --record-sample (aidev_sample_prs manifest).
set -uo pipefail
cd "$(dirname "$0")"

LOG="run_conversation_pipeline.log"; : > "$LOG"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
declare -A STATUS

run_stage() {                       # run_stage <abort|warn> <name> <cmd...>
  local mode="$1"; local name="$2"; shift 2
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
  for s in reset stage3b stage3c stage5b elicited verify export; do
    printf '  %-10s %s\n' "$s" "${STATUS[$s]:-SKIPPED}" | tee -a "$LOG"
  done
  echo "full log: $(pwd)/$LOG" | tee -a "$LOG"
}

echo "[$(ts)] conversation pipeline start" | tee -a "$LOG"
run_stage abort reset    reset_aidev_review_classification.py
run_stage abort stage3b  stage3b_conversational.py
run_stage abort stage3c  stage3c_reviewer_flags.py
run_stage abort stage5b  stage5b_conversation_resolution.py
run_stage warn  elicited analyze_reviewer_elicited_satd.py
run_stage warn  verify   verify_conv_pipeline.py
run_stage warn  export   export_satd_conversations.py
summary
