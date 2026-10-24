"""Backfill coding-agent attribution onto EXISTING code-comment instances, from the DB alone.

Stamps introducer_kind / agent_name / committed_by on satd_instances that predate the
agents.py change — no patch files or clones needed (unlike intro_hunk/fix_hunk/revisits,
which require re-running stage3/stage5 on the box that has data/raw_diffs + clones).

Run:  python backfill_attribution.py
"""
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import config


def run(db=None):
    conn = sqlite3.connect(db or str(config.DB_PATH)); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT i.id, p.author AS pr_author, p.author_type,
               cm.author_login, cm.committer_login, cm.co_authors
        FROM satd_instances i
        LEFT JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr
        LEFT JOIN commits cm ON cm.sha=i.introducing_sha
        WHERE i.channel='code_comment'
    """).fetchall()
    kinds = Counter()
    for r in rows:
        ids = [r["pr_author"], r["author_type"], r["author_login"], r["committer_login"], r["co_authors"]]
        kind, aname = agents.classify_actor(*ids)
        committed = agents.committed_by(r["author_login"], r["committer_login"])
        conn.execute("UPDATE satd_instances SET introducer_kind=?, agent_name=?, committed_by=? WHERE id=?",
                     (kind, aname, committed, r["id"]))
        kinds[kind] += 1
    # revisit counts from already-recorded satd_events (AI = coding-agent touches after intro).
    # Only for TRACKED instances (status set); untracked stay NULL. Going forward stage5 fills
    # these live with the corrected (automation-excluded) actor classification.
    conn.execute("""
        UPDATE satd_instances SET
          n_ai_revisits=(SELECT COUNT(*) FROM satd_events e WHERE e.instance_id=satd_instances.id AND e.actor_class='ai'),
          n_ai_revisit_prs=(SELECT COUNT(DISTINCT e.event_pr) FROM satd_events e WHERE e.instance_id=satd_instances.id AND e.actor_class='ai')
        WHERE status IS NOT NULL""")
    conn.commit()
    print(f"backfilled {len(rows)} code-comment instances:")
    for k, v in kinds.most_common():
        print(f"  {k:16} {v}")
    rev = conn.execute("SELECT COUNT(*) t, SUM(CASE WHEN n_ai_revisits>0 THEN 1 ELSE 0 END) r "
                       "FROM satd_instances WHERE introducer_kind='coding_agent' AND n_ai_revisits IS NOT NULL").fetchone()
    print(f"  agent-introduced tracked={rev['t']}, of which AI-revisited={rev['r'] or 0}")
    conn.close()


if __name__ == "__main__":
    run()
