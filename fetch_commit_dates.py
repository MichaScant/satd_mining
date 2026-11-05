"""Backfill the TRUE commit date for agent code-SATD introducing commits.

The elicited-vs-spontaneous timing currently compares a developer's review-comment time
against the commit's git AUTHOR date (commits.authored_at). Author date can be set to when
the agent started the branch and is preserved across rebases, so it's a weak proxy for
"when the comment actually entered the PR". The git COMMITTER date is updated whenever the
commit object is (re)written (fresh commit, amend, rebase), so it's a much closer proxy for
push/PR-visible time. This fetches commit.committer.date for each introducing commit and
stores it in commits.committed_at.

  GITHUB_TOKEN=... python3 fetch_commit_dates.py

Only ~984 commits to fetch (well within the 5000/hr authenticated budget, ~12 min).
Resumable: skips commits that already have committed_at set.
"""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config
from stage2_fetch import _get   # rate-limit-aware GitHub GET

DB = HERE / "db" / "satd_mining.db"
BASE = "channel='code_comment' AND introducer_kind='coding_agent'"


def ensure_col(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(commits)")]
    if "committed_at" not in cols:
        conn.execute("ALTER TABLE commits ADD COLUMN committed_at TEXT")
        conn.commit()
        print("added column commits.committed_at")


def run():
    if not config.GITHUB_TOKEN:
        print("ERROR: set GITHUB_TOKEN=... and re-run"); return
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    ensure_col(conn)
    rows = conn.execute(
        f"SELECT DISTINCT i.repo repo, i.introducing_sha sha "
        f"FROM satd_instances i JOIN commits cm ON cm.sha = i.introducing_sha "
        f"WHERE {BASE} AND i.introducing_sha IS NOT NULL AND cm.committed_at IS NULL").fetchall()
    total = len(rows)
    print(f"commits to fetch: {total}")
    done = fail = 0
    for n, r in enumerate(rows, 1):
        st, js = _get(f"{config.GITHUB_API}/repos/{r['repo']}/commits/{r['sha']}")
        cdate = None
        if js and isinstance(js, dict) and js.get("commit"):
            cdate = (js["commit"].get("committer") or {}).get("date")
        if cdate:
            conn.execute("UPDATE commits SET committed_at=? WHERE sha=?", (cdate, r["sha"]))
            done += 1
        else:
            fail += 1   # 404/DMCA/deleted commit — leaves committed_at NULL (falls back to authored_at)
        if n % 50 == 0:
            conn.commit()
            print(f"  {n}/{total}  ok={done} fail={fail}")
    conn.commit()
    print(f"done: ok={done} fail={fail} of {total}")
    # quick check: how many agent code instances now have a true commit date
    cov = conn.execute(
        f"SELECT SUM(cm.committed_at IS NOT NULL) have, COUNT(*) tot "
        f"FROM satd_instances i JOIN commits cm ON cm.sha=i.introducing_sha WHERE {BASE}").fetchone()
    print(f"agent code instances with true commit date: {cov['have']}/{cov['tot']}")
    conn.close()


if __name__ == "__main__":
    run()
