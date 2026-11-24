"""Stage 3f — for each VALID + CLOSED issue (stage3d/3e), find the PR that closed it, confirm it was
closed BY A MERGE, and count AI commits to that fixing PR before the close (the revisit metric).

Per the agreed rule, an issue only counts as resolving debt if it was closed by a merged PR (not
stale/wontfix). We resolve the closing PR cheaply:
  1. LOCAL first — a resolution-intent link ("Closes #N") to this issue whose PR is merged (prs table).
     Covers most cases with ZERO API calls.
  2. GitHub fallback — GET /issues/{N}/timeline: take a cross-referenced/connected PR that is merged
     (checked locally, else one /pulls/{n} fetch), or a 'closed' event carrying a commit_id (closed by
     landed code) as a weaker merge signal.
Then n_ai_revisits_issue = AI-authored commits to that closing PR with authored_at <= issue closed_at
  (AI = author_login is a coding agent; local commits table if the PR is ours, else /pulls/{n}/commits).

Writes columns on aidev_issue_links: closing_pr_repo, closing_pr_number, closed_by_merge,
n_ai_revisits_issue. Resumable (aidev_issue_closing_log). Fail-fast on bad auth.

  GITHUB_TOKEN=... python stage3f_issue_closing_pr.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import config
from db import store
from stage2_fetch import _get

_PR_URL = re.compile(r"/repos/([^/\s]+/[^/\s]+)/(?:pulls|issues)/(\d+)")


def _add_cols(conn):
    for col, decl in (("closing_pr_repo", "TEXT"), ("closing_pr_number", "INTEGER"),
                      ("closed_by_merge", "INTEGER"), ("n_ai_revisits_issue", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE aidev_issue_links ADD COLUMN {col} {decl}")
        except Exception:
            pass
    conn.execute("CREATE TABLE IF NOT EXISTS aidev_issue_closing_log (issue_repo TEXT, "
                 "issue_number INTEGER, PRIMARY KEY (issue_repo, issue_number))")
    conn.commit()


def _merged_local(conn, repo, num):
    r = conn.execute("SELECT merged_at FROM prs WHERE repo=? AND pr_number=?", (repo, num)).fetchone()
    return (r is not None, bool(r and r["merged_at"]))        # (in_our_data, is_merged)


def _ai_commits_local(conn, repo, num, before):
    r = conn.execute("SELECT id FROM prs WHERE repo=? AND pr_number=?", (repo, num)).fetchone()
    if not r:
        return None                                           # PR not in our data -> caller fetches
    return sum(1 for c in conn.execute(
        "SELECT author_login, authored_at FROM commits WHERE pr_id=?", (r["id"],))
        if c["author_login"] and agents.agent_name(c["author_login"])
        and c["authored_at"] and (not before or c["authored_at"] <= before))


def _ai_commits_remote(repo, num, before):
    n = 0; page = 1
    while True:
        code, data = _get(f"{config.GITHUB_API}/repos/{repo}/pulls/{num}/commits?per_page=100&page={page}")
        if not isinstance(data, list) or not data:
            break
        for cm in data:
            login = ((cm.get("author") or {}) or {}).get("login")
            date = ((cm.get("commit") or {}).get("author") or {}).get("date")
            if login and agents.agent_name(login) and date and (not before or date <= before):
                n += 1
        if len(data) < 100:
            break
        page += 1
    return n


def _closing_pr_from_timeline(repo, num):
    """Return (pr_repo, pr_number, had_commit_close) from the issue timeline, or (None, None, bool)."""
    page = 1; prs = []; had_commit = False
    while True:
        code, data = _get(f"{config.GITHUB_API}/repos/{repo}/issues/{num}/timeline?per_page=100&page={page}")
        if code in (401, 403):
            raise PermissionError(code)
        if not isinstance(data, list) or not data:
            break
        for ev in data:
            et = ev.get("event")
            if et == "closed" and ev.get("commit_id"):
                had_commit = True
            if et in ("cross-referenced", "connected", "referenced"):
                src = (ev.get("source") or {}).get("issue") or {}
                if src.get("pull_request"):                    # the cross-ref is a PR
                    url = src.get("repository_url") or src.get("url") or ""
                    m = _PR_URL.search(src.get("html_url") or "") or _PR_URL.search(url)
                    pr_repo = (src.get("repository") or {}).get("full_name")
                    if not pr_repo and src.get("html_url"):
                        mm = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", src["html_url"])
                        if mm:
                            pr_repo, prs_num = mm.group(1), int(mm.group(2)); prs.append((pr_repo, prs_num)); continue
                    if src.get("number"):
                        prs.append((pr_repo or repo, int(src["number"])))
        if len(data) < 100:
            break
        page += 1
    return prs, had_commit


def run():
    conn = store.connect(); store.init_db(conn); _add_cols(conn)
    code, rl = _get(f"{config.GITHUB_API}/rate_limit")
    if code in (401, 403):
        print(f"ABORT: GitHub auth failed (HTTP {code}). Set a VALID GITHUB_TOKEN. Nothing written."); sys.exit(1)
    rem = ((rl or {}).get("resources", {}).get("core", {}) or {}).get("remaining")
    print(f"token OK — core remaining: {rem}", flush=True)

    issues = conn.execute(
        "SELECT DISTINCT issue_repo, issue_number, closed_at FROM aidev_issue_links "
        "WHERE valid=1 AND state='closed' AND closed_at IS NOT NULL").fetchall()
    done = {(r["issue_repo"], r["issue_number"]) for r in
            conn.execute("SELECT issue_repo, issue_number FROM aidev_issue_closing_log")}
    todo = [r for r in issues if (r["issue_repo"], r["issue_number"]) not in done]
    print(f"{len(issues)} valid+closed issues; {len(done)} done; {len(todo)} to resolve closing-PR", flush=True)

    n_merge = n_local = n_api = n_nomerge = 0
    for i, r in enumerate(todo, 1):
        irepo, inum, closed_at = r["issue_repo"], r["issue_number"], r["closed_at"]
        closing = None; by_merge = 0

        # 1. LOCAL: a "Closes #issue" PR of ours that is merged
        for cand in conn.execute(
            "SELECT DISTINCT repo, pr_number FROM aidev_issue_links WHERE issue_repo=? AND issue_number=? "
            "AND intent='resolution'", (irepo, inum)):
            _, merged = _merged_local(conn, cand["repo"], cand["pr_number"])
            if merged:
                closing = (cand["repo"], cand["pr_number"]); by_merge = 1; n_local += 1; break

        # 2. GitHub timeline fallback
        if not closing:
            try:
                cand_prs, had_commit = _closing_pr_from_timeline(irepo, inum)
            except PermissionError as e:
                print(f"ABORT: auth failed mid-run (HTTP {e}). Fix token, re-run (resumable)."); conn.commit(); sys.exit(1)
            n_api += 1
            for pr_repo, pr_num in cand_prs:
                in_data, merged = _merged_local(conn, pr_repo, pr_num)
                if not in_data:                                 # confirm merge via one fetch
                    c2, d2 = _get(f"{config.GITHUB_API}/repos/{pr_repo}/pulls/{pr_num}")
                    merged = bool(isinstance(d2, dict) and d2.get("merged_at"))
                if merged:
                    closing = (pr_repo, pr_num); by_merge = 1; break
            if not closing and had_commit:
                by_merge = 1                                    # closed by a landed commit (no PR pinned)

        # 3. AI-commit revisit count to the closing PR before the issue closed
        nrev = 0
        if closing:
            cr, cn = closing
            nrev = _ai_commits_local(conn, cr, cn, closed_at)
            if nrev is None:
                nrev = _ai_commits_remote(cr, cn, closed_at)
            n_merge += 1
        else:
            n_nomerge += 1

        conn.execute(
            "UPDATE aidev_issue_links SET closing_pr_repo=?, closing_pr_number=?, closed_by_merge=?, "
            "n_ai_revisits_issue=? WHERE issue_repo=? AND issue_number=?",
            (closing[0] if closing else None, closing[1] if closing else None, by_merge, nrev, irepo, inum))
        conn.execute("INSERT OR IGNORE INTO aidev_issue_closing_log VALUES (?,?)", (irepo, inum))
        if i % 25 == 0:
            conn.commit()
            print(f"  {i}/{len(todo)} | merge-closed={n_merge} (local={n_local} api={n_api}) "
                  f"not-by-merge={n_nomerge}", flush=True)
    conn.commit()

    v = conn.execute("SELECT COUNT(*) FROM aidev_issue_links WHERE valid=1 AND closed_by_merge=1").fetchone()[0]
    print(f"\ndone. merge-closed issue links={n_merge} (local={n_local}, via API={n_api}), "
          f"not-by-merge={n_nomerge}")
    print(f"  valid links now gated closed_by_merge=1: {v}")
    print("Next: re-run stage6 — issue resolution is now gated on closed_by_merge and carries the "
          "AI-commit revisit count.")
    conn.close()


if __name__ == "__main__":
    run()
