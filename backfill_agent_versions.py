"""Backfill commits.agent_version by mining text we ALREADY have — PR bodies, review comments,
and (for newly-fetched commits) commit messages. No refetch. Stamps the detected model/version
onto the coding-agent commits of any PR whose text names one. Best-effort and idempotent: most
PRs name no model, so most commits stay NULL (that's expected — the raw text is stored so this
can be re-run as the extractor improves).

  python3 backfill_agent_versions.py
"""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agents
from db import store


def run():
    conn = store.connect(); store.init_db(conn); conn.row_factory = sqlite3.Row
    ver = {}   # pr_id -> version (first source wins: pr body > review events > commit message)
    for r in conn.execute("SELECT id, body FROM prs WHERE body IS NOT NULL"):
        v = agents.extract_agent_version(r["body"])
        if v:
            ver[r["id"]] = v
    for r in conn.execute("SELECT pr_id, body FROM review_events WHERE body IS NOT NULL"):
        if r["pr_id"] in ver:
            continue
        v = agents.extract_agent_version(r["body"])
        if v:
            ver[r["pr_id"]] = v
    for r in conn.execute("SELECT pr_id, message FROM commits WHERE message IS NOT NULL"):
        if r["pr_id"] in ver:
            continue
        v = agents.extract_agent_version(r["message"])
        if v:
            ver[r["pr_id"]] = v

    stamped = 0
    for pr_id, v in ver.items():
        cur = conn.execute(
            "UPDATE commits SET agent_version=? WHERE pr_id=? AND agent_version IS NULL "
            "AND author_class='ai'", (v, pr_id))
        stamped += cur.rowcount
    conn.commit()
    print(f"PRs with a detected version: {len(ver)}   commits stamped: {stamped}")
    print("version distribution on commits:")
    for r in conn.execute("SELECT agent_version, COUNT(*) n FROM commits "
                          "WHERE agent_version IS NOT NULL GROUP BY agent_version ORDER BY n DESC LIMIT 25"):
        print(f"   {r['agent_version']:26} {r['n']}")
    conn.close()


if __name__ == "__main__":
    run()
