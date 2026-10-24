#!/usr/bin/env bash
# One-shot setup for the AI technical-debt mining pipeline.
# Installs Python deps, checks system tools, inits the SQLite schema, and fetches
# the SWE-Review-Chat dataset (unless already present).
#
#   bash setup.sh
#
# After it finishes, export a GitHub token for Stages 2 & 5:
#   export GITHUB_TOKEN=ghp_...
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] Python dependencies (polars, requests, GitPython, ...)"
pip install -U -r requirements.txt

echo "[2/4] System tools"
command -v git >/dev/null 2>&1 || { echo "  ERROR: git not found (needed for Stage 5 clones)"; exit 1; }
echo "  git: $(git --version)"

echo "[3/4] SQLite schema"
python3 db/store.py

echo "[4/4] SWE-Review-Chat dataset (~1.7 GB parquet)"
if ls data/swe-review-chat/*.parquet >/dev/null 2>&1; then
  echo "  already present — skipping."
else
  pip install -U huggingface_hub
  if command -v hf >/dev/null 2>&1; then
    hf download Suzhen/SWE-Review-Chat --repo-type dataset --local-dir data/swe-review-chat
  else
    huggingface-cli download Suzhen/SWE-Review-Chat --repo-type dataset --local-dir data/swe-review-chat
  fi
fi

echo
echo "Setup complete. Next:"
echo "  export GITHUB_TOKEN=ghp_...          # for stages 2 & 5"
echo "  python3 stage1_select.py --limit 30  # smoke test"
echo "  python3 stage2_fetch.py  --limit 30"
echo "  python3 stage3_satd_scan.py        # Channel A: code-comment SATD"
echo "  python3 stage3b_conversational.py  # Channel B: conversational SATD (no GitHub)"
echo "  python3 stage3c_reviewer_flags.py"
echo "  python3 stage5_track.py  --limit 20"
