"""Remove machine-generated tool/review/summary posts that leaked into the AGENT introduction channel
(agent_reply / pr_body). These are not genuine agent debt admissions — the agent 'noticing its own
debt' means a code comment or a real dialogue statement, not a Cursor/Codex/Bugbot/validation post.

Checks the FULL source text (utterance body / PR title+body), not the truncated snippet, so markers
beyond 200 chars are still caught. Independent review-bot flags (actor_type='ai_reviewer') are left
untouched — those are the legitimate 'bot points out already-introduced debt' role.

  python reclassify_tool_posts.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import satd_detect as sd
from db import store


def run():
    conn = store.connect()
    rows = conn.execute(
        "SELECT i.id, i.source_kind, i.utterance_id uid, i.repo, i.introducing_pr pr "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='conversation' "
        "AND i.source_kind IN ('agent_reply','pr_body')").fetchall()
    print(f"checking {len(rows)} agent-channel conversation SATD for tool/summary posts ...", flush=True)

    to_del = []; to_reviewbot = []
    for r in rows:
        if r["source_kind"] == "agent_reply" and r["uid"] is not None:
            ev = conn.execute("SELECT body FROM review_events WHERE id=?", (r["uid"],)).fetchone()
            text = ev["body"] if ev else ""
        else:                                   # pr_body -> full PR title+body
            pr = conn.execute("SELECT title, body FROM prs WHERE repo=? AND pr_number=?",
                              (r["repo"], r["pr"])).fetchone()
            text = " ".join(x for x in ((pr["title"] if pr else None), (pr["body"] if pr else None)) if x)
        if sd.is_review_bot_post(text):          # review-bot finding -> move to reviewbot channel
            to_reviewbot.append(r["id"])
        elif sd.is_tool_post(text):              # summary/validation/CI noise -> delete
            to_del.append((r["id"], r["source_kind"]))

    from collections import Counter
    by_sk = Counter(sk for _, sk in to_del)
    conn.executemany(
        "UPDATE satd_instances SET source_kind='ai_reviewer_flag', circumstance='flagged_by_ai_reviewer' "
        "WHERE id=?", [(i,) for i in to_reviewbot])
    conn.executemany("DELETE FROM satd_instances WHERE id=?", [(i,) for i, _ in to_del])
    conn.commit()
    print(f"moved {len(to_reviewbot)} review-bot posts from agent channel -> reviewbot (ai_reviewer_flag)")
    print(f"deleted {len(to_del)} summary/noise posts from the agent channel: {dict(by_sk)}")
    # remaining agent-channel conversation SATD
    rem = conn.execute(
        "SELECT i.source_kind, COUNT(*) FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='conversation' GROUP BY 1 ORDER BY 2 DESC").fetchall()
    print("remaining conversation SATD by source_kind:")
    for sk, n in rem:
        print(f"  {sk}: {n}")
    conn.close()


if __name__ == "__main__":
    run()
