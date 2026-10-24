"""Stage 3b — Channel B: conversational SATD in PR text and review_conversations.

Agents (and developers talking to them) admit debt in words, not just in code:
"left the tests as a follow-up", "hard-coded for now", "just hardcode it for this PR".
This stage scans text already loaded by Stage 1 — no GitHub fetch needed — using the
same marker/phrase core as Channel A plus a conversational-deferral list, after
stripping fenced/inline code (so a quoted `// TODO` is not miscounted). Each hit is a
conversational SATD instance (channel='conversation'):

  source_kind = pr_body         (PR title/body; actor = PR author)
              | agent_reply      (utterance with actor_type='ai')
              | developer_request (utterance with actor_type='human' -> circumstance
                                   'requested_by_developer')

Reviewer flags (a reviewer pointing out debt the author did NOT admit) stay in Stage 3c
(reviewer_flags) and are NEVER mixed into SATD counts — per the methodology doc.

Conversational admissions have no git sha, so Stage 5's pickaxe tracker skips them
(channel filter); tracking them over time is a separate, fuzzier step (doc §3) — many
will honestly end up 'untrackable'.

Run:  python stage3b_conversational.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: F401
import satd_detect as sd
from db import store


def _store_conv(conn, repo, pr_number, intro_sentinel, path, raw, actor_class,
                source_kind, ts, utterance_id=None, circumstance=None):
    kw = sd.detect_conversational(raw)
    if not kw:
        return 0
    norm = sd.normalize(sd.strip_code(raw))
    if not norm:
        return 0
    inst_id = store.upsert_satd_instance(
        conn, repo=repo, introducing_sha=intro_sentinel, path=path,
        comment_text_norm=norm[:300], comment_text_raw=sd.strip_code(raw).strip()[:200],
        introducing_pr=pr_number, keyword=kw, satd_type=sd.classify_type(raw),
        channel="conversation", source_kind=source_kind, utterance_id=utterance_id,
        author_class=actor_class, circumstance=circumstance,
    )
    store.enqueue_validation(conn, "flagged_satd_conv", "satd_instances", inst_id,
                             {"repo": repo, "pr": pr_number, "source_kind": source_kind,
                              "keyword": kw, "text": sd.strip_code(raw)[:300]})
    return 1


def run():
    conn = store.connect()
    n_body = n_ai = n_dev = 0

    n_prs = conn.execute("SELECT COUNT(*) n FROM prs").fetchone()["n"]
    n_utt = conn.execute("SELECT COUNT(*) n FROM review_events WHERE body IS NOT NULL").fetchone()["n"]
    print(f"scanning {n_prs} PR bodies + {n_utt} utterances ...", flush=True)

    # (1) PR title + body — admission by the PR author
    for i, p in enumerate(conn.execute(
        "SELECT id, repo, pr_number, author_type, title, body FROM prs"
    ).fetchall(), 1):
        text = " ".join(x for x in (p["title"], p["body"]) if x)
        n_body += _store_conv(
            conn, p["repo"], p["pr_number"], f"prbody:{p['id']}",
            f"__pr__/{p['pr_number']}", text, p["author_type"], "pr_body", None,
        )
        if i % 20000 == 0:
            conn.commit()
            print(f"  PR bodies {i}/{n_prs} | hits so far: {n_body}", flush=True)
    conn.commit()

    # (2)/(3) utterances — agent replies (admission) and developer requests
    for j, e in enumerate(conn.execute(
        "SELECT e.id, e.pr_id, e.ts, e.actor_type, e.file_path, e.body, "
        "p.repo, p.pr_number FROM review_events e JOIN prs p ON p.id=e.pr_id "
        "WHERE e.body IS NOT NULL"
    ).fetchall(), 1):
        if e["actor_type"] == "ai":
            source_kind, circumstance = "agent_reply", None
        elif e["actor_type"] == "human":
            source_kind, circumstance = "developer_request", "requested_by_developer"
        else:
            continue
        path = e["file_path"] or f"__pr__/{e['pr_number']}"
        got = _store_conv(
            conn, e["repo"], e["pr_number"], f"utt:{e['id']}", path, e["body"],
            e["actor_type"], source_kind, e["ts"], utterance_id=e["id"],
            circumstance=circumstance,
        )
        if got:
            if source_kind == "agent_reply":
                n_ai += got
            else:
                n_dev += got
        if j % 50000 == 0:
            conn.commit()
            print(f"  utterances {j}/{n_utt} | agent={n_ai} dev={n_dev}", flush=True)

    conn.commit()
    tot = n_body + n_ai + n_dev
    print(f"conversational SATD: {tot}  (pr_body={n_body}, agent_reply={n_ai}, developer_request={n_dev})")
    # per-actor-type conversational rate (NEW — no published baseline; report with validation)
    for at in ("ai", "human"):
        utt = conn.execute("SELECT COUNT(*) n FROM review_events WHERE actor_type=? AND body IS NOT NULL",
                           (at,)).fetchone()["n"] or 1
        hit = conn.execute("SELECT COUNT(*) n FROM satd_instances WHERE channel='conversation' AND author_class=?"
                           " AND source_kind IN ('agent_reply','developer_request')", (at,)).fetchone()["n"]
        print(f"  {at:>5} utterances: {hit}/{utt} = {100*hit/utt:.2f}% admitted debt")
    conn.close()


if __name__ == "__main__":
    run()
