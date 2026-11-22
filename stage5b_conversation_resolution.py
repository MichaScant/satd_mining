"""Stage 5b — resolution of CONVERSATION-channel SATD (what Stage 5's pickaxe skips).

Stage 5 tracks code-comment debt via git. Debt raised/admitted in the PR conversation has no sha,
so this stage classifies it from the conversation + the linked code debt, using your definition:

  FIX      : the debt was addressed after it was raised — EITHER the linked code SATD comment was
             removed (Stage 5 status) OR the agent CONFIRMED removal in a reply
             (satd_detect.is_debt_removal, the literature-grounded first-person/action + removal-verb
             + debt-term detector) — AND the developer did not object before merge.
  DISPUTED : a fix was claimed/made BUT a reviewer requested changes (review_state=CHANGES_REQUESTED)
             — i.e. "no, you fixed it wrong". Not counted as resolved.
  REVISIT  : the agent engaged the debt again (mentioned/worked the debt term) but did not confirm
             removal and the code comment is still present, with no developer objection.
  ALIVE    : none of the above.

Also records the number of conversation turns up to the fix (n_events) and the agent turns
(n_ai_revisits) — the "how many conversations before the fix" metric (RQ2). Conversation turns are
ordered by review_events.ts (AIDev review comments DO carry created_at/submitted_at — only commit
dates are missing), so the count is temporal where timestamps exist.

Anchored by (repo, pr, path[, position]) so a conversation mention and the code SATD at the same
location are treated as the same debt item.

  python stage5b_conversation_resolution.py [--cohort aidev]
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import satd_detect as sd
from db import store

_RESOLVED_CODE = {"resolved", "file_deleted", "comment_deleted_only", "self_resolved_premerge"}


def run(cohort=None):
    conn = store.connect()
    conn.execute("CREATE INDEX IF NOT EXISTS ix_re_prid ON review_events(pr_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_si_rpp ON satd_instances(repo, introducing_pr, path)")
    conn.commit()

    q = ("SELECT i.id, i.repo, i.introducing_pr AS pr, i.path, p.id AS pr_dbid "
         "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
         "WHERE i.channel='conversation'")
    params = ()
    if cohort:
        q += " AND p.cohort=?"; params = (cohort,)
    rows = conn.execute(q, params).fetchall()
    print(f"classifying {len(rows)} conversation SATD instances"
          + (f" (cohort={cohort})" if cohort else ""), flush=True)

    # cache review events per PR (one scan per distinct PR, not per instance)
    ev_cache = {}

    def events(pr_dbid):
        if pr_dbid not in ev_cache:
            ev_cache[pr_dbid] = conn.execute(
                "SELECT actor_type, body, review_state, ts FROM review_events WHERE pr_id=?",
                (pr_dbid,)).fetchall()
        return ev_cache[pr_dbid]

    stats = Counter()
    for n, r in enumerate(rows, 1):
        evs = events(r["pr_dbid"])

        # AI confirmation of removal (earliest), developer objection, agent engagement with the debt
        ai_conf_ts = None
        agent_engaged = False
        dev_objected = any((e["review_state"] or "").upper() == "CHANGES_REQUESTED" for e in evs)
        for e in evs:
            if e["actor_type"] == "ai" and e["body"]:
                if sd.is_debt_removal(e["body"]):
                    agent_engaged = True
                    if ai_conf_ts is None or (e["ts"] and e["ts"] < ai_conf_ts):
                        ai_conf_ts = e["ts"] or ai_conf_ts
                elif sd._DEBT_TERM.search(e["body"]):
                    agent_engaged = True

        # linked code SATD at the same location -> did its comment get removed?
        code = conn.execute(
            "SELECT status, resolver_class FROM satd_instances "
            "WHERE repo=? AND introducing_pr=? AND path=? AND channel='code_comment' "
            "AND status IS NOT NULL ORDER BY (status IN ('resolved','file_deleted')) DESC LIMIT 1",
            (r["repo"], r["pr"], r["path"])).fetchone()
        code_removed = bool(code and code["status"] in _RESOLVED_CODE)

        # a fix = confirmed removal OR the linked code comment was removed (engagement alone isn't)
        fixed = code_removed or (ai_conf_ts is not None)

        # conversation turns up to the fix (temporal where ts exists; else all turns)
        fix_ts = ai_conf_ts
        rounds = sum(1 for e in evs if e["body"] and (not fix_ts or not e["ts"] or e["ts"] <= fix_ts))
        ai_rounds = sum(1 for e in evs if e["actor_type"] == "ai" and e["body"]
                        and (not fix_ts or not e["ts"] or e["ts"] <= fix_ts))

        if fixed and dev_objected:
            status = "conv_disputed"
        elif fixed:
            status = "conv_fixed"
        elif agent_engaged and not dev_objected:
            status = "conv_revisit"
        else:
            status = "conv_alive"
        resolver = (code["resolver_class"] if code_removed and code else ("ai" if ai_conf_ts else None))

        store.update_satd_status(conn, r["id"], status=status, resolver_class=resolver,
                                 n_events=rounds, n_ai_revisits=ai_rounds)
        stats[status] += 1
        if n % 20000 == 0:
            conn.commit(); print(f"  {n}/{len(rows)}  {dict(stats)}", flush=True)

    conn.commit()
    print(f"\ndone. conversation-SATD resolution: {dict(stats)}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=None, help="only classify this cohort, e.g. aidev")
    run(**vars(ap.parse_args()))
