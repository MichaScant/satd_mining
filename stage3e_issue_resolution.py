"""Stage 3e — GitHub half of the issue channel: fetch each referenced issue's resolution state and
VALIDATE the numeric cross-reference, per the strict linkage rule.

Reads the candidate PR->issue links from stage3d (table aidev_issue_links) and, for each DISTINCT
issue, makes ONE call: GET /repos/{issue_repo}/issues/{N}. That response carries everything we need
(state, closed_at, title, body, and a `pull_request` key iff the number is really a PR), so no extra
timeline call is required for the body back-reference.

Validity (a link is kept ONLY via an explicit numeric cross-reference — never word-matching alone):
  (b) PR -> issue : the PR referenced the issue, in debt context, with CREATE/FILE intent
                    ('deferral' from stage3d) -> "opened to address the SATD".
  (a) issue -> PR : the fetched ISSUE body references the PR back  (issue_ref_confirmed)
                    AND the issue itself reads as debt            (issue_debt, satd_detect signal).
  valid = is_issue AND ( intent=='deferral'  OR  (issue_ref_confirmed AND issue_debt) )

Numbers that resolve to a PR (`pull_request` present) are marked is_issue=0 and never count as an
issue-channel link. Resolution of a valid link = issue state 'closed' with its closed_at.

Resumable: issues already in aidev_issue_fetch_log are skipped. Fail-fast on bad auth.

  GITHUB_TOKEN=... python stage3e_issue_resolution.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import satd_detect as sd
from db import store
from stage2_fetch import _get


def _add_col(conn, col, decl):
    try:
        conn.execute(f"ALTER TABLE aidev_issue_links ADD COLUMN {col} {decl}")
    except Exception:
        pass  # already exists


def _issue_is_debt(title, body):
    text = sd.strip_code(" ".join(x for x in (title, body) if x))
    return 1 if (sd.detect_conversational(text) or sd._DEBT_TERM.search(text)) else 0


def _backrefs_pr(issue_text, issue_repo, pr_repo, pr_number):
    """Does the issue body reference THIS PR back? (numeric cross-reference, direction a)"""
    if not issue_text:
        return 0
    pats = [rf"github\.com/{re.escape(pr_repo)}/pull/{pr_number}\b",
            rf"\b{re.escape(pr_repo)}#{pr_number}\b"]
    if issue_repo == pr_repo:                      # same-repo bare #N is unambiguous only in-repo
        pats.append(rf"(?:^|[\s(\[])#{pr_number}\b")
    return 1 if any(re.search(p, issue_text, re.I) for p in pats) else 0


def run():
    conn = store.connect(); store.init_db(conn)
    for col, decl in (("issue_debt", "INTEGER"), ("valid", "INTEGER"), ("is_pr", "INTEGER")):
        _add_col(conn, col, decl)
    conn.execute("CREATE TABLE IF NOT EXISTS aidev_issue_fetch_log (issue_repo TEXT, issue_number INTEGER, "
                 "PRIMARY KEY (issue_repo, issue_number))")
    conn.commit()

    code, rl = _get(f"{config.GITHUB_API}/rate_limit")
    if code in (401, 403):
        print(f"ABORT: GitHub auth failed (HTTP {code}). Set a VALID GITHUB_TOKEN and retry. "
              f"Nothing written."); sys.exit(1)
    rem = ((rl or {}).get("resources", {}).get("core", {}) or {}).get("remaining")
    print(f"token OK — core remaining: {rem}", flush=True)

    issues = [(r["issue_repo"], r["issue_number"]) for r in conn.execute(
        "SELECT DISTINCT issue_repo, issue_number FROM aidev_issue_links")]
    done = {(r["issue_repo"], r["issue_number"]) for r in
            conn.execute("SELECT issue_repo, issue_number FROM aidev_issue_fetch_log")}
    todo = [x for x in issues if x not in done]
    print(f"{len(issues)} distinct referenced issues; {len(done)} already fetched; {len(todo)} to go",
          flush=True)

    n_issue = n_pr = n_gone = n_valid = n_resolved = 0
    for i, (irepo, inum) in enumerate(todo, 1):
        code, data = _get(f"{config.GITHUB_API}/repos/{irepo}/issues/{inum}")
        if code in (401, 403):
            print(f"ABORT: auth failed mid-run (HTTP {code}). Fix token and re-run (resumable). "
                  f"{n_issue} issues saved."); conn.commit(); sys.exit(1)
        if code in (404, 410):                       # issue gone -> log so we don't retry
            conn.execute("INSERT OR IGNORE INTO aidev_issue_fetch_log VALUES (?,?)", (irepo, inum))
            n_gone += 1
            if i % 100 == 0: conn.commit()
            continue
        if not isinstance(data, dict):               # transient (5xx) -> do NOT log; retry next run
            continue

        is_pr = 1 if data.get("pull_request") else 0
        is_issue = 0 if is_pr else 1
        state = data.get("state"); closed_at = data.get("closed_at")
        idebt = _issue_is_debt(data.get("title"), data.get("body"))
        issue_text = " ".join(x for x in (data.get("title"), data.get("body")) if x)
        if is_issue:
            n_issue += 1
        else:
            n_pr += 1

        # update every candidate row for this issue (per-row: back-ref + validity depend on the PR)
        rows = conn.execute(
            "SELECT id, repo, pr_number, intent FROM aidev_issue_links "
            "WHERE issue_repo=? AND issue_number=?", (irepo, inum)).fetchall()
        for r in rows:
            backref = _backrefs_pr(issue_text, irepo, r["repo"], r["pr_number"]) if is_issue else 0
            valid = 1 if (is_issue and (r["intent"] == "deferral" or (backref and idebt))) else 0
            conn.execute(
                "UPDATE aidev_issue_links SET is_issue=?, is_pr=?, state=?, closed_at=?, "
                "issue_debt=?, issue_ref_confirmed=?, valid=? WHERE id=?",
                (is_issue, is_pr, state, closed_at, idebt, backref, valid, r["id"]))
            if valid:
                n_valid += 1
                if state == "closed" and closed_at:
                    n_resolved += 1
        conn.execute("INSERT OR IGNORE INTO aidev_issue_fetch_log VALUES (?,?)", (irepo, inum))
        if i % 100 == 0:
            conn.commit()
            print(f"  {i}/{len(todo)} | issues={n_issue} not-issue(PR)={n_pr} gone={n_gone} "
                  f"| valid links={n_valid} resolved(closed)={n_resolved}", flush=True)
    conn.commit()

    # final tally from the table (authoritative, counts the full dataset incl. prior runs)
    v = conn.execute("SELECT COUNT(*) FROM aidev_issue_links WHERE valid=1").fetchone()[0]
    vr = conn.execute("SELECT COUNT(*) FROM aidev_issue_links WHERE valid=1 AND state='closed' "
                      "AND closed_at IS NOT NULL").fetchone()[0]
    print(f"\ndone. this run: issues={n_issue} PRs-not-issues={n_pr} gone={n_gone}")
    print(f"  VALID issue-tracked SATD links (whole table): {v}  of which resolved (issue closed): {vr}")
    print("  (valid = numeric cross-ref confirmed: PR->issue deferral, or issue->PR back-ref + debt)")
    print("Next: join valid+closed links onto their SATD instances to add issue-channel resolutions.")
    conn.close()


if __name__ == "__main__":
    run()
