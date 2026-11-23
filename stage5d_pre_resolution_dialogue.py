"""Stage 5d — dev<->agent dialogue about a SATD BEFORE it was resolved.

For each resolved SATD (resolved_at known), count the PR review messages exchanged BEFORE the
resolution timestamp, split by actor:
  n_dev_msgs_pre        human developer turns
  n_agent_msgs_pre      the PR's own authoring agent (actor_type='ai')
  n_aireviewer_msgs_pre AI review bots (actor_type='ai_reviewer')

Scope = "in relation to this SATD": inline comments on the SATD's own file + general PR comments
(inline comments on OTHER files are excluded). Fully local — review_events.ts and resolved_at are
already in the DB, no git fetch needed.

  python stage5d_pre_resolution_dialogue.py [--cohort aidev]
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import store


def run(cohort=None):
    conn = store.connect(); store.init_db(conn)    # ensure the n_*_msgs_pre columns exist
    conn.execute("CREATE INDEX IF NOT EXISTS ix_re_prid ON review_events(pr_id)")
    conn.commit()

    q = ("SELECT i.id, i.path, i.resolved_at, p.id AS pr_dbid "
         "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
         "WHERE i.resolved_at IS NOT NULL")
    params = ()
    if cohort:
        q += " AND p.cohort=?"; params = (cohort,)
    rows = conn.execute(q, params).fetchall()
    print(f"{len(rows)} resolved SATD with a resolution timestamp"
          + (f" (cohort={cohort})" if cohort else ""), flush=True)

    ev_cache = {}
    def events(pr_dbid):
        if pr_dbid not in ev_cache:
            ev_cache[pr_dbid] = conn.execute(
                "SELECT actor_type, file_path, ts FROM review_events WHERE pr_id=? AND ts IS NOT NULL",
                (pr_dbid,)).fetchall()
        return ev_cache[pr_dbid]

    stats = Counter(); n = 0
    for r in rows:
        path, rat = r["path"], r["resolved_at"]
        nd = na = nr = 0
        for e in events(r["pr_dbid"]):
            if e["ts"] >= rat:
                continue
            fp = e["file_path"]
            if fp is not None and fp != path:       # inline comment on a different file -> not this SATD
                continue
            at = e["actor_type"]
            if at == "human":        nd += 1
            elif at == "ai":         na += 1
            elif at == "ai_reviewer": nr += 1
        store.update_satd_status(conn, r["id"],
                                 n_dev_msgs_pre=nd, n_agent_msgs_pre=na, n_aireviewer_msgs_pre=nr)
        stats["has_dialogue"] += 1 if (nd + na + nr) else 0
        n += 1
        if n % 20000 == 0:
            conn.commit(); print(f"  {n}/{len(rows)}", flush=True)
    conn.commit()
    # quick summary
    agg = conn.execute(
        "SELECT AVG(n_dev_msgs_pre), AVG(n_agent_msgs_pre), AVG(n_aireviewer_msgs_pre) "
        "FROM satd_instances WHERE n_dev_msgs_pre IS NOT NULL"
        + (" AND introducing_pr IN (SELECT pr_number FROM prs WHERE cohort='%s')" % cohort if cohort else "")
    ).fetchone()
    print(f"\ndone. {n} SATD stamped; {stats['has_dialogue']} had >=1 pre-resolution message.")
    print(f"  mean pre-resolution messages — dev={agg[0] or 0:.2f}  agent={agg[1] or 0:.2f}  "
          f"ai_reviewer={agg[2] or 0:.2f}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=None)
    run(**vars(ap.parse_args()))
