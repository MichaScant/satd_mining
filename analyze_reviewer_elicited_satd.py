"""How often an AI agent NOTICES its own technical debt, REVISITS (removes) it, and — critically —
whether it did so SPONTANEOUSLY or was PROMPTED (elicited). Driven by the stored 3-way actor_type
(ai = the PR's own authoring agent; ai_reviewer = an AI review bot; human), so it works for both
Suzhen (author == agent, no ai_reviewer) and AIDev (human PR author, agent label separate, AI
reviewers present).

For each self-agent admission/removal we scan the FULL preceding PR context and classify the trigger:
  human-elicited      : a developer named debt / alluded to an error / requested changes / raised a concern
  ai_reviewer-elicited: an AI review bot named debt or raised a concern BEFORE the agent admitted  (NEW)
  spontaneous         : no prior signal — the agent noticed on its own

  python analyze_reviewer_elicited_satd.py
"""
import csv
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import satd_detect as sd

HERE = Path(__file__).resolve().parent


def _days(a, b):
    if not a or not b:
        return None
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return round((db - da).total_seconds() / 86400, 2)
    except Exception:
        return None


ERR = re.compile(r"\b(doesn'?t work|not working|broken|fail\w*|\berror\b|\bbug\b|\bwrong\b|incorrect|"
    r"crash\w*|exception|not right|unexpected|regression|typo|not correct|misses)\b", re.I)
CONCERN = re.compile(r"\?|\b(why|what|hmm+|weird|odd|strange|confus\w*|unclear|not sure|"
    r"should (we|this|it|you|be)|could you|can you|please|consider|instead of|rather than|"
    r"needs? to|remove this|change this|revert)\b", re.I)

# display order (strongest/most-specific first); human and ai_reviewer are distinct elicitation classes
ORDER = ["dev named debt", "ai_reviewer named debt", "dev alluded to error", "dev changes_requested",
         "dev raised concern/question", "ai_reviewer raised concern",
         "neutral dev comment before", "neutral ai_reviewer comment before", "SPONTANEOUS (no signal)"]
HUMAN_ELICIT = {"dev named debt", "dev alluded to error", "dev changes_requested",
                "dev raised concern/question", "neutral dev comment before"}
AIREV_ELICIT = {"ai_reviewer named debt", "ai_reviewer raised concern", "neutral ai_reviewer comment before"}


def has_debt(t):
    return sd.detect_conversational(t) is not None


def _cr(kind, review_state):
    """changes-requested in either convention: Suzhen kind='changes_requested' or AIDev review_state."""
    return kind == "changes_requested" or (str(review_state or "").upper() == "CHANGES_REQUESTED")


def trigger(tl, i):
    """(circumstance, triggered_at) from the turns BEFORE index i.
    tl row = (actor_type, text, kind, ts, review_state). Distinguishes human vs ai_reviewer elicitation."""
    t_hd = t_he = t_hc = t_cr = t_h = None   # human: named-debt / error / concern / changes-req / any
    t_rd = t_rc = t_r = None                 # ai_reviewer: named-debt / concern / any
    for j in range(i):
        at, tj, kj, tsj, rs = tl[j]
        if _cr(kj, rs) and at == "human" and t_cr is None:
            t_cr = tsj
        if at == "human":
            if t_h is None: t_h = tsj
            if has_debt(tj) and t_hd is None: t_hd = tsj
            if ERR.search(tj) and t_he is None: t_he = tsj
            if CONCERN.search(tj) and t_hc is None: t_hc = tsj
        elif at == "ai_reviewer":
            if t_r is None: t_r = tsj
            if (has_debt(tj) or ERR.search(tj)) and t_rd is None: t_rd = tsj
            if CONCERN.search(tj) and t_rc is None: t_rc = tsj
    if t_hd is not None: return "dev named debt", t_hd
    if t_rd is not None: return "ai_reviewer named debt", t_rd
    if t_he is not None: return "dev alluded to error", t_he
    if t_cr is not None: return "dev changes_requested", t_cr
    if t_hc is not None: return "dev raised concern/question", t_hc
    if t_rc is not None: return "ai_reviewer raised concern", t_rc
    if t_h is not None: return "neutral dev comment before", t_h
    if t_r is not None: return "neutral ai_reviewer comment before", t_r
    return "SPONTANEOUS (no signal)", None


def code_trigger(conn, pr_id, authored_at):
    """Trigger for a CODE-comment SATD: scan prompter turns (human or ai_reviewer) in the PR BEFORE
    the introducing commit. Returns a circumstance label. (AIDev code commits have no date -> ts is
    NULL -> no preceding turns resolvable -> SPONTANEOUS; a known AIDev limitation.)"""
    if not pr_id or not authored_at:
        return "SPONTANEOUS (no signal)"
    tl = [(e["actor_type"], sd.strip_code(e["body"] or ""), e["kind"], e["ts"], e["review_state"])
          for e in conn.execute(
            "SELECT actor_type, kind, body, ts, review_state FROM review_events "
            "WHERE pr_id=? AND ts IS NOT NULL AND ts<=? AND body IS NOT NULL ORDER BY ts, id",
            (pr_id, authored_at))]
    return trigger(tl + [("ai", "", None, None, None)], len(tl))[0]   # classify over all prior turns


def preceded_by_convo_admission(conn, pr_id, authored_at):
    """Did the authoring agent already self-admit this debt in conversation BEFORE the commit?"""
    if not pr_id or not authored_at:
        return False
    for e in conn.execute(
            "SELECT body FROM review_events WHERE pr_id=? AND actor_type='ai' AND ts IS NOT NULL "
            "AND ts<? AND body IS NOT NULL ORDER BY ts, id", (pr_id, authored_at)):
        if sd.is_self_admission(sd.strip_code(e["body"] or "")):
            return True
    return False


def run():
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    conn.row_factory = sqlite3.Row

    # PRs that contain at least one SELF-agent comment (actor_type='ai')
    agent_prs = [r[0] for r in conn.execute(
        "SELECT DISTINCT pr_id FROM review_events WHERE actor_type='ai' AND body IS NOT NULL")]
    print(f"scanning {len(agent_prs)} PRs with a self-agent turn ...", flush=True)

    notice = {"dialogue": Counter(), "pr_desc": Counter()}
    revisit = {"dialogue": Counter(), "pr_desc": Counter()}
    rows = []
    for pr in agent_prs:
        evs = [dict(r) for r in conn.execute(
            "SELECT e.actor_type at, e.body body, e.kind kind, e.ts ts, e.file_path fp, "
            "e.review_state rs, p.repo repo, p.pr_number prn, p.agent agent "
            "FROM review_events e JOIN prs p ON p.id=e.pr_id "
            "WHERE e.pr_id=? AND e.body IS NOT NULL ORDER BY e.ts, e.id", (pr,))]
        if not evs:
            continue
        tl = [(e["at"], sd.strip_code(e["body"] or ""), e["kind"], e["ts"], e["rs"]) for e in evs]
        for i, e in enumerate(evs):
            if e["at"] != "ai":                 # only the PR's own authoring agent speaks here
                continue
            txt = tl[i][1]
            bucket = "pr_desc" if e["kind"] == "created" else "dialogue"
            adm = sd.is_self_admission(txt)
            rem = sd.is_debt_removal(txt)
            if not (adm or rem):
                continue
            prior = tl[:i]
            counts = {"n_msgs_before": len(prior),
                      "n_dev_msgs_before": sum(1 for p in prior if p[0] == "human"),
                      "n_aireviewer_msgs_before": sum(1 for p in prior if p[0] == "ai_reviewer"),
                      "n_ai_msgs_before": sum(1 for p in prior if p[0] == "ai")}
            trg, trg_at = trigger(tl, i)
            for act, hit, dest in (("notice", adm, notice), ("revisit", rem, revisit)):
                if hit:
                    dest[bucket][trg] += 1
                    rows.append({"act": act, "bucket": bucket, "trigger": trg, "channel": "conversation",
                                 "repo": e["repo"], "pr": e["prn"], "agent": e["agent"], "file_path": e["fp"],
                                 "noticed_at": e["ts"], "triggered_at": trg_at,
                                 "latency_days": _days(trg_at, e["ts"]), **counts, "text": hit})

    # code channel: phase + trigger (persist circumstance). AIDev lacks commit dates -> mostly spontaneous.
    code = {r["ph"]: r["n"] for r in conn.execute(
        "SELECT COALESCE(cm.phase,'no_commit') ph, COUNT(*) n FROM satd_instances i "
        "LEFT JOIN commits cm ON cm.sha=i.introducing_sha "
        "WHERE i.channel='code_comment' AND i.introducer_kind='coding_agent' GROUP BY ph")}

    code_trg = Counter(); n_ft = 0
    for inst in conn.execute(
            "SELECT i.id id, cm.pr_id pr_id, COALESCE(cm.committed_at, cm.authored_at) ts "
            "FROM satd_instances i LEFT JOIN commits cm ON cm.sha=i.introducing_sha "
            "WHERE i.channel='code_comment' AND i.introducer_kind='coding_agent'").fetchall():
        if preceded_by_convo_admission(conn, inst["pr_id"], inst["ts"]):
            circ = "AFTER convo admission (follow-through)"; n_ft += 1
        else:
            circ = code_trigger(conn, inst["pr_id"], inst["ts"])
        code_trg[circ] += 1
        conn.execute("UPDATE satd_instances SET circumstance=? WHERE id=?", (circ, inst["id"]))
    conn.commit()

    pct = lambda n, d: f"{100*n/d:.1f}%" if d else "n/a"

    def show(title, cnt):
        tot = sum(cnt.values())
        print(f"\n{title}: {tot}")
        for t in ORDER:
            if cnt.get(t, 0):
                print(f"    {t:34} {cnt[t]:5}  ({pct(cnt[t], tot)})")
        he = sum(cnt.get(t, 0) for t in HUMAN_ELICIT)
        re_ = sum(cnt.get(t, 0) for t in AIREV_ELICIT)
        sp = cnt.get("SPONTANEOUS (no signal)", 0)
        print(f"    --> human-elicited {he} ({pct(he,tot)})  ai_reviewer-elicited {re_} ({pct(re_,tot)})  "
              f"spontaneous {sp} ({pct(sp,tot)})")

    print("=" * 70)
    print("  AI debt-noticing / revisiting — SPONTANEOUS vs ELICITED (human / ai_reviewer)")
    print("=" * 70)
    print("\n### NOTICING (admitting debt it leaves) ###")
    show("  dialogue", notice["dialogue"])
    print(f"  PR descriptions (separate): {sum(notice['pr_desc'].values())}")
    print("\n### REVISITING (removing debt) ###")
    show("  dialogue", revisit["dialogue"])
    FT = "AFTER convo admission (follow-through)"
    indep = Counter({k: v for k, v in code_trg.items() if k != FT})
    print(f"\n### CODE-COMMENT SATD by trigger (n={sum(code_trg.values())}) ###")
    print(f"  follow-through (already admitted in convo): {n_ft}")
    show("  independent code-noticing", indep)

    out = HERE / "agent_notice_revisit.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["act", "bucket", "trigger", "channel", "repo", "pr", "agent",
                                          "file_path", "noticed_at", "triggered_at", "latency_days",
                                          "n_msgs_before", "n_dev_msgs_before", "n_aireviewer_msgs_before",
                                          "n_ai_msgs_before", "text"])
        w.writeheader(); w.writerows(rows)
    print(f"\n{len(rows)} rows -> {out.name}")
    conn.close()


if __name__ == "__main__":
    run()
