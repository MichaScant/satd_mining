"""Stage 5f — fold the issue channel into resolution: map each VALID + CLOSED issue link
(aidev_issue_links from stage3d/3e) onto the SATD instance that referenced it.

Mapping (tightest first, so we attribute to the debt that actually pointed at the issue):
  1. link came from a conversation turn (utterance_id set) -> the conversation SATD with that same
     utterance_id (the turn that deferred the debt to the issue).
  2. link came from the PR body -> the pr_body conversation SATD of that PR.
  3. fallback -> any still-unresolved conversation SATD in that PR.

Only instances that are NOT already resolved get updated (code/physical resolution wins; we never
overwrite a git-verified removal). Marked: status='resolved_via_issue', resolved_at=issue closed_at,
resolver_class='issue_tracker', resolved_sha='<issue_repo>#<issue_number>' (sentinel, no git sha).

Idempotent: re-running only (re)stamps instances still unresolved. Run after stage3e.

  python stage5f_issue_join.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import store

_UNRESOLVED = ("alive", "untrackable", "conv_alive", "conv_revisit")


def _pick_instance(conn, link):
    repo, pr, utt, src = link["repo"], link["pr_number"], link["utterance_id"], link["source_kind"]
    # 1. the exact turn that referenced the issue
    if utt is not None:
        r = conn.execute(
            "SELECT id, status FROM satd_instances WHERE utterance_id=? AND channel='conversation'",
            (utt,)).fetchone()
        if r:
            return r
    # 2. PR-body admission
    if src == "pr_body":
        r = conn.execute(
            "SELECT id, status FROM satd_instances WHERE repo=? AND introducing_pr=? "
            "AND channel='conversation' AND source_kind='pr_body' LIMIT 1", (repo, pr)).fetchone()
        if r:
            return r
    # 3. any still-unresolved conversation SATD in the PR
    return conn.execute(
        "SELECT id, status FROM satd_instances WHERE repo=? AND introducing_pr=? "
        "AND channel='conversation' AND (status IS NULL OR status IN "
        "('alive','untrackable','conv_alive','conv_revisit')) LIMIT 1", (repo, pr)).fetchone()


def run():
    conn = store.connect(); store.init_db(conn)
    links = conn.execute(
        "SELECT repo, pr_number, issue_repo, issue_number, source_kind, utterance_id, closed_at "
        "FROM aidev_issue_links WHERE valid=1 AND state='closed' AND closed_at IS NOT NULL").fetchall()
    print(f"{len(links)} valid+closed issue links to map", flush=True)

    stats = Counter()
    for lk in links:
        inst = _pick_instance(conn, lk)
        if not inst:
            stats["no_instance"] += 1
            continue
        if inst["status"] not in (None, *_UNRESOLVED):
            stats["already_resolved_skip"] += 1      # code/physical resolution wins
            continue
        store.update_satd_status(
            conn, inst["id"], status="resolved_via_issue", resolved_at=lk["closed_at"],
            resolver_class="issue_tracker",
            resolved_sha=f"{lk['issue_repo']}#{lk['issue_number']}")
        stats["resolved_via_issue"] += 1
    conn.commit()

    print(f"\ndone. {dict(stats)}")
    # per-agent tally of the new issue-channel resolutions
    rows = conn.execute(
        "SELECT p.agent, COUNT(*) n FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE i.status='resolved_via_issue' GROUP BY p.agent ORDER BY n DESC").fetchall()
    print("issue-channel resolutions by authoring agent:")
    for r in rows:
        print(f"   {str(r['agent']):18} {r['n']}")
    conn.close()


if __name__ == "__main__":
    run()
