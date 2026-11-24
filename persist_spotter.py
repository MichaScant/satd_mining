"""Persist the final RQ classification onto each AIDev SATD so all graphs read one source of truth:

  spotter      agent | dev | reviewbot        — WHO spotted the debt (RQ1, Level 1)
  how_spotted  spontaneous | dev_elicited | reviewbot_elicited   — for AGENT-spotted only (RQ1, Level 2);
               NULL for dev/reviewbot-spotted.

Definitions (locked):
  spotter   = code_comment -> agent; conversation agent_reply/pr_body -> agent;
              developer_request -> dev; ai_reviewer_flag -> reviewbot.
  how       (agent-spotted): reviewbot-elicited if a reviewbot pointed at THIS debt; else dev_elicited
              if a dev pointed at it in-thread OR a human developer opened/initiated the PR (dev directed
              the agent — a dev-created PR is not spontaneous); else spontaneous.

RQ2 (resolution/revisits) is then simply: filter spotter='agent', group by how_spotted, read outcome +
n_revisits_resolve.

  python persist_spotter.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
from db import store

_DEV = {"requested_by_developer", "dev named debt", "dev alluded to error",
        "dev raised concern/question", "dev changes_requested", "neutral dev comment before"}
_REV = {"flagged_by_ai_reviewer", "ai_reviewer named debt", "ai_reviewer raised concern",
        "neutral ai_reviewer comment before"}


def _spotter(channel, sk):
    if channel == "code_comment":
        return "agent"
    if sk == "developer_request":
        return "dev"
    if sk == "ai_reviewer_flag":
        return "reviewbot"
    return "agent"


def _how(circ, author_kind):
    if circ in _REV:
        return "reviewbot_elicited"
    if circ in _DEV or author_kind == "human":
        return "dev_elicited"
    return "spontaneous"


def run():
    conn = store.connect()
    for col in ("spotter", "how_spotted"):
        try:
            conn.execute(f"ALTER TABLE satd_instances ADD COLUMN {col} TEXT")
        except Exception:
            pass
    conn.commit()

    rows = conn.execute(
        "SELECT i.id, i.channel ch, i.source_kind sk, i.circumstance circ, p.author author "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev'").fetchall()
    n = 0
    from collections import Counter
    sp_c = Counter(); how_c = Counter()
    for r in rows:
        sp = _spotter(r["ch"], r["sk"])
        if sp == "agent":
            ak = agents.classify_actor(r["author"] or "")[0]
            how = _how(r["circ"], ak)
        else:
            how = None
        conn.execute("UPDATE satd_instances SET spotter=?, how_spotted=? WHERE id=?", (sp, how, r["id"]))
        sp_c[sp] += 1
        if how:
            how_c[how] += 1
        n += 1
        if n % 20000 == 0:
            conn.commit()
    conn.commit()

    # Fold the developer-initiated-PR signal into `circumstance` so EVERY circumstance-based graph
    # (code triggers, type-elicited, elicited-vs-spontaneous, …) is consistent with how_spotted, not
    # just notice_triggers_db. An agent-spotted debt that is dev_elicited ONLY because the PR was
    # human-opened (no in-thread dev signal -> its circumstance was still SPONTANEOUS/NULL/follow-through)
    # is relabelled 'developer-initiated PR'. In-thread dev signals keep their specific circumstance.
    relabel = conn.execute(
        "UPDATE satd_instances SET circumstance='developer-initiated PR' "
        "WHERE spotter='agent' AND how_spotted='dev_elicited' AND (circumstance='SPONTANEOUS (no signal)' "
        "OR circumstance IS NULL OR circumstance='AFTER convo admission (follow-through)')").rowcount
    conn.commit()
    print(f"persisted spotter/how_spotted on {n} AIDev SATD")
    print(f"  spotter: {dict(sp_c)}")
    print(f"  how_spotted (agent-spotted): {dict(how_c)}")
    print(f"  relabelled {relabel} spontaneous->developer-initiated PR (human-opened, no in-thread signal)")
    conn.close()


if __name__ == "__main__":
    run()
