"""Central config for the AI tech-debt mining pipeline.

Paths are relative to this package dir so the pipeline is portable. Secrets and
machine-specific locations come from environment variables.
"""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
DATA_DIR = PKG_DIR / "data"
RAW_DIFFS_DIR = DATA_DIR / "raw_diffs"      # archived patches (reproducibility)
CLONES_DIR = DATA_DIR / "clones"            # full-history repo clones for Stage 5
DB_PATH = Path(os.environ.get("SATD_DB", PKG_DIR / "db" / "satd_mining.db"))
SCHEMA_PATH = PKG_DIR / "db" / "schema.sql"

# --- datasets (download separately — see README) ---
# SWE-Review-Chat (Suzhen): source of truth for PR selection + author labels
SWE_REVIEW_CHAT_PARQUET = Path(
    os.environ.get("SWE_REVIEW_CHAT", DATA_DIR / "swe-review-chat")   # dir of parquet shards
)
# AIDev: baselines only, NOT a pipeline dependency
AIDEV_DIR = Path(os.environ.get("AIDEV_DIR", DATA_DIR / "aidev"))

# --- GitHub API ---
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API = "https://api.github.com"
FETCH_MAX_PATCH_BYTES = 1_000_000           # skip/flag 'too_large' diffs above this

# --- SATD detection (added lines only) ---
SATD_KEYWORDS = ["todo", "fixme", "hack", "xxx", "workaround", "kludge", "fix me",
                 "bug", "broken", "temporary", "temp", "refactor", "revisit"]

# --- published baselines (AIDev / Brahmi), for sanity counters only ---
BASELINE_AI_SATD_RATE = 0.026               # 2.6% of AI commits
BASELINE_HUMAN_SATD_RATE = 0.0425           # 4.25% of human commits
BASELINE_SILENT_HIT_RATE = 0.091            # ~9.1% / commit

# --- pin tool versions for the quality-drop tier (Stage 3b) ---
TOOL_VERSIONS = {
    "jscpd": "", "lizard": "", "ruff": "", "pylint": "", "eslint": "",
    "pmd": "", "checkstyle": "", "sonarqube": "",
}

for _d in (DATA_DIR, RAW_DIFFS_DIR, CLONES_DIR, DB_PATH.parent):
    _d.mkdir(parents=True, exist_ok=True)
