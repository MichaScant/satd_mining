"""How often an AI agent NOTICES its own technical debt, and the CIRCUMSTANCES — plus how
often it REVISITS (removes) debt. Two tracked acts, each with its trigger context.

  NOTICING   (is_self_admission): agent admits debt it is leaving in its own change.
  REVISITING (is_debt_removal):   agent removes/resolves a debt item.

For each, we log the trigger by scanning the FULL preceding PR context (dev named debt /
alluded to an error / raised a concern-or-question / changes_requested / any human / none).
Coding agents only, authoring agent only, code blocks stripped. Dialogue = replies/comments
(PR descriptions reported separately — they're scope narration, noisier).

  python analyze_reviewer_elicited_satd.py
"""
import csv
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import satd_detect as sd

HERE = Path(__file__).resolve().parent

ERR = re.compile(r"\b(doesn'?t work|not working|broken|fail\w*|\berror\b|\bbug\b|\bwrong\b|incorrect|"
    r"crash\w*|exception|not right|unexpected|regression|typo|not correct|misses)\b", re.I)
CONCERN = re.compile(r"\?|\b(why|what|hmm+|weird|odd|strange|confus\w*|unclear|not sure|"
    r"should (we|this|it|you|be)|could you|can you|please|consider|instead of|rather than|"
    r"needs? to|remove this|change this|revert)\b", re.I)
ORDER = ["dev named debt", "dev alluded to error", "changes_requested",
         "dev raised concern/question", "neutral dev comment before", "SPONTANEOUS (no dev signal)"]


def has_debt(t):
    return sd.detect_conversational(t) is not None


def _classify(pd, pe, pcr, pc, ph):
    if pd: return "dev named debt"
    if pe: return "dev alluded to error"
    if pcr: return "changes_requested"
    if pc: return "dev raised concern/question"
    if ph: return "neutral dev comment before"
    return "SPONTANEOUS (no dev signal)"


def trigger(tl, i):
    pd = pe = pc = pcr = ph = False
    for j in range(i):
        kj, tj, rkj = tl[j][0], tl[j][1], tl[j][3]
        if rkj == "changes_requested":
            pcr = True
        if kj == "human":
            ph = True
            if has_debt(tj): pd = True
            if ERR.search(tj): pe = True
            if CONCERN.search(tj): pc = True
    return _classify(pd, pe, pcr, pc, ph)


def code_trigger(conn, pr_id, authored_at):
    """Same taxonomy as trigger(), but for a CODE-comment SATD instance: scan the
    DEVELOPER turns in the PR that happened BEFORE the introducing commit."""
    if not pr_id or not authored_at:
        return "SPONTANEOUS (no dev signal)"
    pd = pe = pcr = pc = ph = False
    for e in conn.execute(
            "SELECT actor, kind, body FROM review_events "
            "WHERE pr_id=? AND ts IS NOT NULL AND ts<=? AND body IS NOT NULL "
            "ORDER BY ts, id", (pr_id, authored_at)):
        if agents.classify_actor(e["actor"])[0] != "human":
            continue
        ph = True
        tj = sd.strip_code(e["body"] or "")
        if e["kind"] == "changes_requested": pcr = True
        if has_debt(tj): pd = True
        if ERR.search(tj): pe = True
        if CONCERN.search(tj): pc = True
    return _classify(pd, pe, pcr, pc, ph)


def preceded_by_convo_admission(conn, pr_id, authored_at, author):
    """Did the authoring agent ALREADY self-admit this debt in the PR conversation
    BEFORE this commit landed? If so, the code comment is a follow-through on that
    conversational admission — NOT an independent code-channel noticing, and counting
    it would double-count against the dialogue admissions."""
    if not pr_id or not authored_at or not author:
        return False
    for e in conn.execute(
            "SELECT actor, body FROM review_events "
            "WHERE pr_id=? AND ts IS NOT NULL AND ts<? AND body IS NOT NULL "
            "ORDER BY ts, id", (pr_id, authored_at)):
        if e["actor"] != author:            # the authoring agent's own turns only
            continue
        if sd.is_self_admission(sd.strip_code(e["body"] or "")):
            return True
    return False


def run():
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    conn.row_factory = sqlite3.Row
    agent_prs = set()
    for r in conn.execute("SELECT DISTINCT pr_id, actor FROM review_events WHERE body IS NOT NULL"):
        if agents.classify_actor(r["actor"])[0] == "coding_agent":
            agent_prs.add(r["pr_id"])

    notice = {"dialogue": Counter(), "pr_desc": Counter()}
    revisit = {"dialogue": Counter(), "pr_desc": Counter()}
    rows = []
    for pr in agent_prs:
        evs = [dict(r) for r in conn.execute(
            "SELECT e.actor actor, e.body body, e.kind kind, p.author author, p.repo repo, p.pr_number prn "
            "FROM review_events e JOIN prs p ON p.id=e.pr_id "
            "WHERE e.pr_id=? AND e.body IS NOT NULL ORDER BY e.ts, e.id", (pr,))]
        if not evs:
            continue
        author = evs[0]["author"]
        if agents.classify_actor(author)[0] != "coding_agent":
            continue
        tl = [(agents.classify_actor(e["actor"])[0], sd.strip_code(e["body"] or ""), e["actor"],
               e["kind"], e["repo"], e["prn"]) for e in evs]
        for i, (k, txt, actor, rk, repo, prn) in enumerate(tl):
            if actor != author:
                continue
            bucket = "pr_desc" if rk == "created" else "dialogue"
            adm = sd.is_self_admission(txt)
            rem = sd.is_debt_removal(txt)
            if adm:
                trg = trigger(tl, i); notice[bucket][trg] += 1
                rows.append({"act": "notice", "bucket": bucket, "trigger": trg,
                             "repo": repo, "pr": prn, "agent": actor, "text": adm})
            if rem:
                trg = trigger(tl, i); revisit[bucket][trg] += 1
                rows.append({"act": "revisit", "bucket": bucket, "trigger": trg,
                             "repo": repo, "pr": prn, "agent": actor, "text": rem})

    # code channel (phase)
    code = {r["ph"]: r["n"] for r in conn.execute(
        "SELECT COALESCE(cm.phase,'no_commit') ph, COUNT(*) n FROM satd_instances i "
        "LEFT JOIN commits cm ON cm.sha=i.introducing_sha "
        "WHERE i.channel='code_comment' AND i.introducer_kind='coding_agent' GROUP BY ph")}

    # code channel (TRIGGER) — same taxonomy as the dialogue channel, computed from
    # the developer turns that preceded the introducing commit. Persist to circumstance.
    code_trg = Counter()
    n_followthrough = 0          # committed AFTER the agent already admitted it in convo
    for inst in conn.execute(
            "SELECT i.id id, cm.pr_id pr_id, cm.authored_at ts, p.author author "
            "FROM satd_instances i "
            "LEFT JOIN commits cm ON cm.sha=i.introducing_sha "
            "LEFT JOIN prs p ON p.id=cm.pr_id "
            "WHERE i.channel='code_comment' AND i.introducer_kind='coding_agent'").fetchall():
        if preceded_by_convo_admission(conn, inst["pr_id"], inst["ts"], inst["author"]):
            circ = "AFTER convo admission (follow-through)"
            n_followthrough += 1
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
            v = cnt.get(t, 0)
            if v:
                print(f"    {t:30} {v:4}  ({pct(v, tot)})")
        sp = cnt.get("SPONTANEOUS (no dev signal)", 0)
        print(f"    --> elicited {tot-sp} ({pct(tot-sp,tot)})   spontaneous {sp} ({pct(sp,tot)})")

    print("=" * 66)
    print("  AI NOTICING vs REVISITING ITS OWN TECHNICAL DEBT")
    print("=" * 66)
    print("\n### NOTICING (admitting debt it leaves) ###")
    show("  dialogue (replies/comments)", notice["dialogue"])
    print(f"  PR descriptions (scope narration, separate): {sum(notice['pr_desc'].values())}")
    print("\n### REVISITING (removing/resolving debt) ###")
    show("  dialogue (replies/comments)", revisit["dialogue"])
    print(f"  PR descriptions: {sum(revisit['pr_desc'].values())}")
    ctot = sum(code.values())
    print(f"\n### CODE-COMMENT SATD by commit phase (n={ctot}) ###")
    print(f"  first_attempt (spontaneous): {code.get('first_attempt',0)}  |  "
          f"revision (elicited): {code.get('revision',0)}  |  human_applied (not agent): {code.get('human_applied',0)}")

    FT = "AFTER convo admission (follow-through)"
    code_total = sum(code_trg.values())
    indep = Counter({k: v for k, v in code_trg.items() if k != FT})   # independent code-noticing
    indep_tot = sum(indep.values())
    print(f"\n### CODE-COMMENT SATD by TRIGGER — same criteria as dialogue (n={code_total}) ###")
    print(f"  EXCLUDED as follow-through (agent already admitted it in convo "
          f"BEFORE this commit): {n_followthrough} ({pct(n_followthrough, code_total)})")
    print(f"  independent code-channel noticing: {indep_tot}")
    show("    of which, by trigger", indep)

    # headline: across BOTH channels, how much of the AI's debt-noticing was
    # developer-driven vs spontaneous — code channel counts INDEPENDENT instances only
    # (follow-throughs are already represented in the dialogue channel).
    def eli_spo(cnt):
        tot = sum(cnt.values()); sp = cnt.get("SPONTANEOUS (no dev signal)", 0)
        return tot, tot - sp, sp
    dt, de, ds = eli_spo(notice["dialogue"])
    kt, ke, ks = eli_spo(indep)
    print("\n" + "=" * 66)
    print("  HEADLINE: how AI debt-noticing was triggered (both channels)")
    print("=" * 66)
    print(f"  conversation admissions    : {dt:5}   elicited {de} ({pct(de,dt)})   spontaneous {ds} ({pct(ds,dt)})")
    print(f"  code-comment (independent) : {kt:5}   elicited {ke} ({pct(ke,kt)})   spontaneous {ks} ({pct(ks,kt)})")
    print(f"  [code follow-throughs already counted in conversation: {n_followthrough}]")
    T, E, S = dt + kt, de + ke, ds + ks
    print(f"  ALL noticing (de-duped)    : {T:5}   elicited {E} ({pct(E,T)})   spontaneous {S} ({pct(S,T)})")

    out = HERE / "agent_notice_revisit.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["act", "bucket", "trigger", "repo", "pr", "agent", "text"])
        w.writeheader(); w.writerows(rows)
    print(f"\n{len(rows)} rows (notice + revisit, with trigger) -> {out.name}")
    conn.close()


if __name__ == "__main__":
    run()
