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

# python3 -u so output streams live through tee (otherwise it buffers and looks "stuck")
# run_stage "stage3b_conversational" python3 -u stage3b_conversational.py   # only if it never finished
run_stage "stage3_satd_scan"       python3 -u stage3_satd_scan.py
run_stage "stage3c_reviewer_flags" python3 -u stage3c_reviewer_flags.py

# disk check before stage5 clones repos (you've hit the quota wall before)
echo "[$(ts)] disk before stage5:" | tee -a "$LOG"
df -h . | tee -a "$LOG"
du -sh data/clones 2>/dev/null | tee -a "$LOG" || true

run_stage "stage5_track"           python3 -u stage5_track.py
# stamp agent attribution + fill revisit counts on already-tracked instances (from events)
run_stage "backfill_attribution"   python3 -u backfill_attribution.py
# fill n_ai_file_edits/n_ai_near_edits on instances tracked before those metrics existed (from patches)
run_stage "backfill_file_edits"    python3 -u backfill_file_edits.py
# stamp commits.agent_version by mining PR bodies / review comments / commit messages (no refetch)
run_stage "backfill_agent_versions" python3 -u backfill_agent_versions.py
# backfill the TRUE commit date (git committer date ~ when the debt entered the PR) for the
# agent code SATD commits, so the elicited-vs-spontaneous timing uses real PR-visible dates.
# No-op without GITHUB_TOKEN (prints a notice); safe/resumable.
run_stage "fetch_commit_dates"     python3 -u fetch_commit_dates.py
# text report + CSV + graphs
run_stage "report"                 python3 -u report.py
# conversational notice/revisit analysis (uses the full conversation data)
run_stage "notice_revisit"         python3 -u analyze_reviewer_elicited_satd.py
# graphs need matplotlib; if it's not on this box, skip cleanly (generate them on your Mac)
if python3 -c "import matplotlib" 2>/dev/null; then
  run_stage "graphs"               python3 -u graph_admissions.py
else
  STATUS[graphs]="SKIPPED (no matplotlib — run graph_admissions.py on your Mac)"
  echo "===== [$(ts)] graphs SKIPPED — matplotlib not installed; graph locally =====" | tee -a "$LOG"
fi

echo ""                                 | tee -a "$LOG"
echo "===== [$(ts)] SUMMARY ====="       | tee -a "$LOG"
for s in stage3_satd_scan stage3c_reviewer_flags stage5_track backfill_attribution backfill_file_edits backfill_agent_versions fetch_commit_dates report notice_revisit graphs; do
  printf '  %-24s %s\n' "$s" "${STATUS[$s]:-SKIPPED}" | tee -a "$LOG"
done
echo "full log: $(pwd)/$LOG" | tee -a "$LOG"
