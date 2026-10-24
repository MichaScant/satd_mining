"""Stage 3c — reviewer flags + first-pointer determination.

(a) Scan review_events bodies (incl. replies) for debt-flagging language -> reviewer_flags,
    recording who flagged (actor_type) and at what level: 'file' (inline comment with a path)
    or 'review' (PR-level comment).
(b) First-pointer per SATD instance: who pointed the debt out FIRST — the self-admission
    (the SATD comment in a commit, level='commit') or the earliest reviewer flag on that file
    (level='file') — classified AI vs human. Answers the email's "who pointed out the issue
    first: AI or human, at the file or commit level."

Run:  python stage3c_reviewer_flags.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: F401 (keeps import path consistent)
from db import store

# debt-flagging language a reviewer uses to point out technical debt
FLAG_RE = {
    "hack":          re.compile(r"\bhack(y|s)?\b|\bkludge\b", re.I),
    "workaround":    re.compile(r"\bwork[- ]?around\b|\btemporary\b|\btemp fix\b|\bfor now\b", re.I),
    "duplication":   re.compile(r"\bduplicat|\bcopy[- ]?paste|\bDRY\b", re.I),
    "missing_tests": re.compile(r"\bmissing tests?\b|\bno tests?\b|\badd (a )?tests?\b|\buntested\b", re.I),
    "todo":          re.compile(r"\bTODO\b|\bFIXME\b|\bshould (be )?fix|\bneeds? (to be )?(fixed|refactor)", re.I),
}


def scan_flags(conn) -> int:
    rows = conn.execute(
        "SELECT id, pr_id, actor_type, file_path, ts, body FROM review_events WHERE body IS NOT NULL"
    ).fetchall()
    n = 0
    for r in rows:
        for cat, rx in FLAG_RE.items():
            if rx.search(r["body"] or ""):
                store.add_reviewer_flag(
                    conn, r["pr_id"], r["id"], cat, actor_type=r["actor_type"],
                    file_path=r["file_path"], ts=r["ts"],
                    level="file" if r["file_path"] else "review",
                )
                n += 1
                break  # one flag per event
    conn.commit()
    return n


def set_first_pointers(conn) -> int:
    """Earliest of (self-admission at commit level) vs (reviewer flag at file level) wins."""
    insts = conn.execute(
        "SELECT i.id, i.repo, i.path, i.introducer_kind, i.author_class, i.introducing_pr, c.authored_at "
        "FROM satd_instances i LEFT JOIN commits c ON c.sha = i.introducing_sha"
    ).fetchall()
    # the self-admitting actor = who introduced the debt (coding agent -> ai), not the legacy
    # author_class 'conflict' bucket
    _kind = {"coding_agent": "ai", "human": "human", "automation_bot": "automation"}
    n = 0
    for i in insts:
        self_ts = i["authored_at"]
        self_actor = _kind.get(i["introducer_kind"], i["author_class"] or "human")
        best = (self_ts, self_actor, "commit") if self_ts else None   # self-admission
        # earliest reviewer flag on the same file WITHIN the introducing PR (a reviewer may have
        # pointed it out before the author added the SATD comment -> human-flagged-first).
        frow = conn.execute(
            "SELECT rf.actor_type, rf.ts FROM reviewer_flags rf JOIN prs p ON p.id = rf.pr_id "
            "WHERE p.repo = ? AND p.pr_number = ? AND rf.file_path = ? AND rf.ts IS NOT NULL "
            "ORDER BY rf.ts LIMIT 1",
            (i["repo"], i["introducing_pr"], i["path"]),
        ).fetchone()
        if frow:
            cand = (frow["ts"], frow["actor_type"], "file")
            if best is None or (cand[0] and best[0] and cand[0] < best[0]):
                best = cand
        if best and best[0]:
            store.update_satd_status(conn, i["id"], first_pointer_ts=best[0],
                                     first_pointer_class=best[1], first_pointer_level=best[2])
            n += 1
    conn.commit()
    return n


def run():
    conn = store.connect()
    nf = scan_flags(conn)
    np = set_first_pointers(conn)
    print(f"reviewer_flags written: {nf} | first-pointer set on {np} instances")
    conn.close()


if __name__ == "__main__":
    run()
