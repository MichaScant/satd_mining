"""Diagnostic: per-agent MERGED-PR headroom for raising the trackable SATD pool to ~1000/agent.

Reads AIDev pull_request.parquet (agent, merged_at, state) and the aidev_sample_prs manifest, and
reports — per authoring agent:
  total PRs | merged PRs | merged PRs already scanned | merged PRs NOT yet scanned (headroom)
plus the current trackable (merged resolved+alive) SATD count from the DB.

This answers: can we realistically reach 1000 MERGED SATD instances per agent, or is an agent
PR-exhausted (ceiling < 1000)?  Run on the SERVER (dataset lives there).

  AIDEV_DIR=data/aidev python3 diag_merged_headroom.py
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polars as pl
import config
from db import store
from stage1_aidev import AIDEV_AGENT_MAP    # dataset label -> our canonical agent name
from stage3_aidev import _repo_num


def run():
    d = config.AIDEV_DIR
    pr = pl.read_parquet(d / "pull_request.parquet", columns=["id", "html_url", "agent", "merged_at", "state"])

    # per agent: total PRs, merged PRs, and the set of merged (repo, pr_number) keys
    total = defaultdict(int); merged = defaultdict(int)
    merged_keys = defaultdict(set)
    for row in pr.iter_rows(named=True):
        lbl = str(row["agent"] or "").strip().lower()
        name = AIDEV_AGENT_MAP.get(lbl)
        if not name:
            continue
        total[name] += 1
        if row["merged_at"]:
            merged[name] += 1
            repo, num = _repo_num(row["html_url"])
            if repo:
                merged_keys[name].add((repo, num))

    conn = store.connect(); store.init_db(conn)
    # PRs we already examined, per agent
    scanned = defaultdict(set)
    try:
        for repo, num, ag in conn.execute("SELECT repo, pr_number, agent FROM aidev_sample_prs"):
            scanned[ag].add((repo, num))
    except Exception as e:
        print("WARN: aidev_sample_prs unreadable:", e)

    # current trackable (merged resolved+alive) SATD per agent
    RES = ('resolved', 'file_deleted', 'comment_deleted_only', 'self_resolved_premerge')
    track = defaultdict(int)
    for ag, st, n in conn.execute(
            "SELECT i.agent_name, i.status, COUNT(*) FROM satd_instances i "
            "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
            "WHERE p.cohort='aidev' AND i.channel='code_comment' AND i.agent_name IS NOT NULL "
            "GROUP BY i.agent_name, i.status"):
        if st in RES or st == 'alive':
            track[ag] += n

    agents = sorted(total, key=lambda a: -total[a])
    print(f"{'agent':10}{'PRs':>8}{'merged':>8}{'merged_scanned':>16}{'merged_HEADROOM':>16}"
          f"{'cur_trackable':>14}")
    for a in agents:
        mk = merged_keys[a]
        scanned_merged = len(mk & scanned[a])
        headroom = len(mk) - scanned_merged
        print(f"{a:10}{total[a]:>8}{merged[a]:>8}{scanned_merged:>16}{headroom:>16}{track.get(a,0):>14}")
    print("\nReading: 'merged' is the ABSOLUTE ceiling of PRs that could yield trackable SATD.")
    print("'merged_HEADROOM' = merged PRs we have NOT scanned yet (new SATD can only come from these).")
    print("If merged_HEADROOM is ~0, the agent is PR-exhausted and cannot reach 1000 — ceiling is fixed.")
    conn.close()


if __name__ == "__main__":
    run()
