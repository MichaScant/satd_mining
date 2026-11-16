"""Stage 1 (AIDev) — load coding-agent PRs from the AIDev dataset into the `prs` table, the SAME
shape stage1_select.py produces for Suzhen's SWE-Review-Chat. Everything downstream (stage2 fetch,
stage3 satd_detect, stage5 track, analysis) is shared and unchanged — only the PR SOURCE differs.

  python stage1_aidev.py --file /path/to/aidev.parquet [--limit N] [--cohort aidev]

Supports .parquet / .csv / .jsonl / .json. EDIT `COLMAP` below to match your AIDev file's column
names (run once with --file to see the available columns if they don't match). Only genuine coding
agents (Copilot/Devin/Claude/Cursor/...) are loaded — same `agents.classify_actor` filter as stage 1.
PRs already in the DB (same repo+pr_number) are SKIPPED so Suzhen rows aren't clobbered. If your
AIDev file has a review-conversation column, set COLMAP['review_conversations']; otherwise AIDev PRs
are code-channel only (stage 2 fetches commits+patches regardless, so code-comment SATD still works).
"""
import argparse
import re
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
from db import store
from stage1_select import _explode_reviews   # reuse Suzhen's conversation exploder (same review schema)

# ---- Pre-wired for the official AIDev (hao-li/AIDev, SAILResearch). Edit if your file differs. ----
COLMAP = {
    "repo": None,                    # derived from pr_url (html_url)
    "pr_number": "number",
    "author": "user",                # PR author login (bot login for Devin/Cursor/Claude/Codex)
    "author_type": None,
    "agent": "agent",                # AIDev's AUTHORITATIVE agent label — used to filter+attribute
    "state": "state",
    "merged_at": "merged_at",
    "created_at": "created_at",
    "title": "title",
    "body": "body",                  # AIDev includes the PR body (useful; agent/version sometimes here)
    "repo_url": "repo_url",
    "review_conversations": None,    # threads live in separate AIDev tables (pr_review_comments etc.), not here
    "pr_url": "html_url",            # https://github.com/owner/name/pull/123 -> derive repo + number
}

# AIDev agent label -> our canonical agent_name (also decides what counts as a coding agent).
# AIDev uses underscores (Claude_Code, OpenAI_Codex, Google_Jules); include both forms.
AIDEV_AGENT_MAP = {
    "openai codex": "codex", "openai_codex": "codex", "codex": "codex",
    "github copilot": "copilot", "copilot": "copilot",
    "claude code": "claude", "claude_code": "claude", "claude": "claude",
    "cursor": "cursor",
    "google jules": "jules", "google_jules": "jules", "jules": "jules",
    "devin": "devin",
}

_PRURL = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)", re.I)


def _frame(path):
    """LazyFrame over a single file OR a directory of shards (AIDev ships many parquet files)."""
    p = Path(path)
    if p.is_dir():
        for ext, fn in ((".parquet", pl.scan_parquet), (".jsonl", pl.scan_ndjson),
                        (".ndjson", pl.scan_ndjson), (".csv", pl.scan_csv)):
            if list(p.rglob(f"*{ext}")):
                return fn(str(p / "**" / f"*{ext}"))
        raise SystemExit(f"no .parquet/.jsonl/.csv files under {p}")
    if p.suffix == ".parquet":
        return pl.scan_parquet(p)
    if p.suffix == ".csv":
        return pl.scan_csv(p, infer_schema_length=10000)
    if p.suffix in (".jsonl", ".ndjson"):
        return pl.scan_ndjson(p)
    if p.suffix == ".json":
        return pl.read_json(p).lazy()
    raise SystemExit(f"unsupported file type {p.suffix!r} (use .parquet/.csv/.jsonl/.json or a dir)")


def _get(row, key):
    col = COLMAP.get(key)
    return row.get(col) if col else None


def _repo_prnum(row):
    repo, prnum = _get(row, "repo"), _get(row, "pr_number")
    if (not repo or prnum in (None, "")) and COLMAP.get("pr_url"):
        m = _PRURL.search(str(row.get(COLMAP["pr_url"]) or ""))
        if m:
            repo, prnum = m.group(1), m.group(2)
    try:
        return (repo, int(prnum)) if repo and prnum not in (None, "") else (None, None)
    except (TypeError, ValueError):
        return None, None


def run(file, limit=None, cohort="aidev", agents_filter=None, exclude=None):
    lf = _frame(file)
    cols = lf.collect_schema().names()
    # validate the column map (repo/number may come from pr_url instead)
    need = [COLMAP[k] for k in ("pr_number", "author", "agent", "pr_url") if COLMAP.get(k)]
    missing = [c for c in need if c not in cols]
    hard_missing = [c for c in missing if c != COLMAP.get("pr_number") or not COLMAP.get("pr_url")]
    if hard_missing:
        print(f"\nERROR: COLMAP columns not in file: {missing}")
        print(f"available columns: {cols}")
        print("-> edit COLMAP at the top of stage1_aidev.py to match, then re-run.")
        return

    # which AIDev agent LABELS to keep (lowercased). Default = every coding agent in the map.
    want = set(AIDEV_AGENT_MAP)
    if agents_filter:                       # keep only these, e.g. --agents devin,claude
        pick = {a.strip().lower() for a in agents_filter.split(",") if a.strip()}
        want = {lbl for lbl, name in AIDEV_AGENT_MAP.items() if name in pick}
    elif exclude:                           # keep everything EXCEPT these, e.g. --exclude copilot
        drop = {a.strip().lower() for a in exclude.split(",") if a.strip()}
        want = {lbl for lbl, name in AIDEV_AGENT_MAP.items() if name not in drop}
    ac = COLMAP.get("agent")
    if ac and ac in cols:                   # lazily drop everything but the wanted agents BEFORE collecting
        lf = lf.filter(pl.col(ac).cast(pl.Utf8).str.to_lowercase().str.strip_chars().is_in(list(want)))
    df = lf.collect()
    print(f"AIDev rows after agent filter ({'all' if not agents_filter else agents_filter}): {df.height}")

    conn = store.init_db()
    existing = {(r[0], r[1]) for r in conn.execute("SELECT repo, pr_number FROM prs")}
    loaded = dup = nonagent = reviews = 0
    for row in df.iter_rows(named=True):
        repo, prnum = _repo_prnum(row)
        if not repo:
            continue
        author, atype = _get(row, "author"), _get(row, "author_type")
        # prefer AIDev's authoritative `agent` label (handles Copilot branch-prefix PRs where the
        # user is human); fall back to classify_actor when no agent column is mapped.
        if COLMAP.get("agent"):
            name = AIDEV_AGENT_MAP.get(str(_get(row, "agent") or "").strip().lower())
            if not name:                      # human baseline / unknown agent -> skip
                nonagent += 1
                continue
        else:
            kind, name = agents.classify_actor(author, atype)
            if kind != "coding_agent":
                nonagent += 1
                continue
        if (repo, prnum) in existing:
            dup += 1
            continue
        pr_id = store.upsert_pr(
            conn, repo=repo, pr_number=prnum, repo_url=_get(row, "repo_url"),
            author=author, author_type="ai", agent=name, state=_get(row, "state"),
            merged_at=_get(row, "merged_at"), created_at=_get(row, "created_at"),
            cohort=cohort, title=_get(row, "title"), body=_get(row, "body"),
        )
        existing.add((repo, prnum))
        reviews += _explode_reviews(conn, pr_id, _get(row, "review_conversations"))
        loaded += 1
        if loaded % 500 == 0:
            conn.commit(); print(f"  loaded {loaded}")
        if limit and loaded >= limit:
            break
    conn.commit()
    print(f"\nloaded {loaded} AIDev coding-agent PRs (cohort={cohort}); "
          f"skipped {dup} already-present, {nonagent} non-agent; {reviews} review_events")
    print("by agent:")
    for r in conn.execute("SELECT COALESCE(agent,'?') a, COUNT(*) n FROM prs WHERE cohort=? GROUP BY a ORDER BY n DESC", (cohort,)):
        print(f"   {str(r[0]):14} {r[1]}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="AIDev file OR directory of parquet shards")
    ap.add_argument("--limit", type=int, default=None, help="cap PRs loaded (smoke test)")
    ap.add_argument("--cohort", default="aidev", help="cohort tag stored on each PR")
    ap.add_argument("--agents", dest="agents_filter", default=None,
                    help="keep ONLY these agents, e.g. devin,claude")
    ap.add_argument("--exclude", default=None,
                    help="keep all agents EXCEPT these, e.g. --exclude copilot")
    run(**vars(ap.parse_args()))
