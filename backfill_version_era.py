"""Stamp each SATD instance with the agent's documented default model on its date.

No refetch: the date already exists — commit date for Suzhen's cohort, PR `created_at` for AIDev
(both already in the DB). We pick the best date available per instance, run it through
agent_versions.infer_default_version(agent, date), and write the result to
satd_instances.agent_version (column added if missing). Cursor/Devin get the explicit
'undisclosed ...' marker; agents/dates with no documented default get NULL.

  python backfill_version_era.py              # stamp all instances
  python backfill_version_era.py --dry-run    # just print the distribution it WOULD write

Idempotent: re-running recomputes from scratch. See agent_versions.DISCLOSURE for the paragraph
to put in the paper's limitations.
"""
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_versions as av
import agents

DB = Path(__file__).resolve().parent / "db" / "satd_mining.db"


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def run(dry_run=False):
    conn = sqlite3.connect(str(DB))
    icols = _cols(conn, "satd_instances")
    for need in ("id", "repo", "introducing_pr", "introducing_sha", "agent_name"):
        if need not in icols:
            raise SystemExit(f"satd_instances is missing column {need!r}; have {sorted(icols)}")
    if "agent_version" not in icols and not dry_run:
        conn.execute("ALTER TABLE satd_instances ADD COLUMN agent_version TEXT")
        conn.commit()

    # date lookups: commit date (preferred) then PR created_at
    sha_date = {}
    if "committed_at" in _cols(conn, "commits"):
        sha_date = {s: d for s, d in conn.execute(
            "SELECT sha, committed_at FROM commits WHERE committed_at IS NOT NULL AND sha IS NOT NULL")}
    # per-PR agent + date in one pass. Resolution order for the agent: prs.agent (set for AIDev),
    # then classify the PR author/login (Suzhen's cohort stores the agent as the bot author, e.g.
    # 'copilot-swe-agent[bot]', with prs.agent left NULL).
    pcols = _cols(conn, "prs")
    acol = "author" if "author" in pcols else None
    atcol = "author_type" if "author_type" in pcols else None
    sel = ["repo", "pr_number", "agent", "created_at"] + [c for c in (acol, atcol) if c]
    pr_date, pr_agent = {}, {}
    for row in conn.execute(f"SELECT {', '.join(sel)} FROM prs"):
        r, n, a, d = row[0], row[1], row[2], row[3]
        if d:
            pr_date[(r, n)] = d
        if not a and acol:                       # fall back to classifying the author login
            author = row[sel.index(acol)]
            atype = row[sel.index(atcol)] if atcol else None
            a = agents.classify_actor(author, atype)[1]
        if a:
            pr_agent[(r, n)] = a
    print(f"date sources: {len(sha_date)} commit dates, {len(pr_date)} PR dates; {len(pr_agent)} PR agents")

    rows = conn.execute(
        "SELECT id, repo, introducing_pr, introducing_sha, agent_name FROM satd_instances").fetchall()
    updates = []
    stamped = Counter()       # (agent, version) -> n
    no_date = Counter()       # agent -> n  (had no usable date)
    for _id, repo, pr, sha, inst_agent in rows:
        agent = inst_agent or pr_agent.get((repo, pr))   # instance label, else the PR's agent
        date = sha_date.get(sha) or pr_date.get((repo, pr))
        if not date:
            no_date[agent] += 1
        ver = av.infer_default_version(agent, date)
        updates.append((ver, _id))
        stamped[(agent, ver)] += 1

    print(f"\ninstances: {len(rows)}")
    print("stamped agent_version distribution (agent -> version: count):")
    for (agent, ver), n in sorted(stamped.items(), key=lambda kv: (kv[0][0] or "", -kv[1])):
        print(f"   {str(agent):8} {str(ver):42} {n}")
    if no_date:
        print("instances with NO usable date (version left NULL unless agent is undisclosed):")
        for agent, n in no_date.most_common():
            print(f"   {str(agent):8} {n}")

    if dry_run:
        print("\n--dry-run: nothing written.")
        conn.close()
        return
    conn.executemany("UPDATE satd_instances SET agent_version=? WHERE id=?", updates)
    conn.commit()
    got = conn.execute("SELECT COUNT(*) FROM satd_instances WHERE agent_version IS NOT NULL").fetchone()[0]
    print(f"\nwrote agent_version for {got}/{len(rows)} instances.")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    run(**vars(ap.parse_args()))
