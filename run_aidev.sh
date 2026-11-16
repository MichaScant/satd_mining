#!/usr/bin/env bash
# Build a BALANCED AIDev SATD sample (N instances per non-copilot agent, default 2000) straight
# from the local parquet (no GitHub fetch), then run the analysis stages over the DB.
#
#   PER_AGENT=2000 bash run_aidev.sh
#   PER_AGENT=2000 EXCLUDE=copilot AIDEV_DIR=data/aidev bash run_aidev.sh
#
# Prereq: stage1_aidev.py already loaded the AIDev PRs into `prs` (cohort='aidev').
# Stages that need GitHub/clones (stage2 fetch, stage5 resolution, fetch_commit_dates) are NOT run
# here — AIDev gives us the code channel locally; resolution tracking for AIDev is a separate step.
set -uo pipefail
cd "$(dirname "$0")"
LOG="run_aidev.log"; : > "$LOG"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
PER_AGENT="${PER_AGENT:-2000}"
EXCLUDE="${EXCLUDE:-copilot}"
AIDEV_DIR="${AIDEV_DIR:-data/aidev}"
declare -A STATUS
run_stage() { local n="$1"; shift; echo "===== [$(ts)] START $n =====" | tee -a "$LOG"
  if "$@" 2>&1 | tee -a "$LOG"; then STATUS[$n]=OK; else STATUS[$n]="FAILED(${PIPESTATUS[0]})"; fi
  echo "===== [$(ts)] END $n — ${STATUS[$n]} =====" | tee -a "$LOG"; }

echo "[$(ts)] AIDev run: PER_AGENT=$PER_AGENT exclude=$EXCLUDE dir=$AIDEV_DIR" | tee -a "$LOG"

# 1. extract N SATD per agent from the AIDev parquet (local, no fetch)
run_stage "stage3_aidev"  python3 -u stage3_aidev.py --aidev-dir "$AIDEV_DIR" --exclude "$EXCLUDE" --per-agent "$PER_AGENT"
# 2. analysis over all satd_instances (now incl. AIDev). notice_revisit writes the dated CSV;
#    AIDev instances have no review threads so they land as code-channel / spontaneous.
run_stage "report"           python3 -u report.py
run_stage "notice_revisit"   python3 -u analyze_reviewer_elicited_satd.py
# 3. graphs (skip cleanly if matplotlib isn't here — generate on your Mac)
if python3 -c "import matplotlib" 2>/dev/null; then
  run_stage "graphs"         python3 -u graph_admissions.py
else
  STATUS[graphs]="SKIPPED (no matplotlib — run graph_admissions.py on your Mac)"
  echo "===== [$(ts)] graphs SKIPPED =====" | tee -a "$LOG"
fi

echo "" | tee -a "$LOG"; echo "===== [$(ts)] SUMMARY =====" | tee -a "$LOG"
for s in stage3_aidev report notice_revisit graphs; do
  printf '  %-16s %s\n' "$s" "${STATUS[$s]:-SKIPPED}" | tee -a "$LOG"
done
echo "AIDev SATD by agent:" | tee -a "$LOG"
python3 - <<'PY' | tee -a "$LOG"
import sqlite3
c=sqlite3.connect("db/satd_mining.db")
for r in c.execute("SELECT i.agent_name,COUNT(*) FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr WHERE p.cohort='aidev' AND i.channel='code_comment' GROUP BY i.agent_name ORDER BY 2 DESC"):
    print(f"   {r[0]:10} {r[1]}")
c.close()
PY
echo "full log: $(pwd)/$LOG" | tee -a "$LOG"
