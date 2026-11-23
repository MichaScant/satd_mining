"""Fetch commit DATES for AIDev SATD PRs from GitHub so revisits can be ordered against resolution.

AIDev ships no commit dates. One GET /repos/{repo}/pulls/{pr}/commits returns all of a PR's commits
WITH author/committer dates, so this is ~one call per SATD-PR (~2.3k), which fits a single 5,000/hr
rate-limit window — single-threaded is correct (GitHub's cap is per-token and concurrent calls trip
its secondary limit, so threads can't beat it). Resumable: PRs already logged are skipped.

  GITHUB_TOKEN=... python fetch_pr_commit_dates.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from db import store
from stage2_fetch import _get   # rate-limit-aware GitHub GET -> (status, json)


def run():
    conn = store.connect(); store.init_db(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS aidev_commit_fetch_log (repo TEXT, pr_number INTEGER, "
                 "PRIMARY KEY (repo, pr_number))")
    conn.commit()

    # fail fast if the token is bad (don't churn 2,337 PRs returning nothing)
    code, rl = _get(f"{config.GITHUB_API}/rate_limit")
    if code in (401, 403):
        print(f"ABORT: GitHub auth failed (HTTP {code}: {rl}). Set a VALID GITHUB_TOKEN and retry. "
              f"Nothing was written.")
        sys.exit(1)
    rem = ((rl or {}).get("resources", {}).get("core", {}) or {}).get("remaining")
    print(f"token OK — core rate-limit remaining: {rem}", flush=True)

    prs = conn.execute(
        "SELECT DISTINCT i.repo, i.introducing_pr FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment'").fetchall()
    done = {(r[0], r[1]) for r in conn.execute("SELECT repo, pr_number FROM aidev_commit_fetch_log")}
    todo = [(r[0], r[1]) for r in prs if (r[0], r[1]) not in done]
    print(f"{len(prs)} SATD-PRs; {len(done)} already fetched; {len(todo)} to go", flush=True)

    n_commits = failed = 0
    for i, (repo, num) in enumerate(todo, 1):
        page = 1; resolved = False
        while True:
            code, data = _get(f"{config.GITHUB_API}/repos/{repo}/pulls/{num}/commits"
                              f"?per_page=100&page={page}")
            if code in (401, 403):
                print(f"ABORT: auth failed mid-run (HTTP {code}). Fix GITHUB_TOKEN and re-run "
                      f"(resumable). {n_commits} dates saved so far.")
                conn.commit(); sys.exit(1)
            if code in (404, 451):            # PR gone / DMCA — legitimately nothing to fetch
                resolved = True; break
            if not isinstance(data, list):    # unexpected (e.g. 5xx exhausted) — do NOT log as done
                break
            resolved = True
            for cm in data:
                sha = cm.get("sha")
                commit = cm.get("commit") or {}
                cdt = (commit.get("committer") or {}).get("date")
                adt = (commit.get("author") or {}).get("date")
                if sha:
                    conn.execute("INSERT OR REPLACE INTO aidev_commit_dates (sha, committed_at, authored_at) "
                                 "VALUES (?,?,?)", (sha, cdt, adt))
                    n_commits += 1
            if len(data) < 100:
                break
            page += 1
        if resolved:                           # only log PRs we actually resolved (fetched or confirmed gone)
            conn.execute("INSERT OR IGNORE INTO aidev_commit_fetch_log (repo, pr_number) VALUES (?,?)", (repo, num))
        else:
            failed += 1
        if i % 100 == 0:
            conn.commit(); print(f"  {i}/{len(todo)} PRs | {n_commits} commit dates | {failed} transient-fail", flush=True)
    conn.commit()
    print(f"done. {n_commits} commit dates stored in aidev_commit_dates. "
          f"Now re-run: stage5c_aidev_revisits.py (it will order revisits before resolution).")
    conn.close()


if __name__ == "__main__":
    run()
