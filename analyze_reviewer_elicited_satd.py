"""What TRIGGERS a coding agent's technical-debt admission? (elicited vs truly spontaneous)

For every agent debt admission we LOG the trigger by scanning the FULL preceding context,
not just the immediately-preceding turn:

  CONVERSATIONAL (authoring agent only): scan every earlier turn in the PR for a developer
    signal — names debt / alludes to an error / raises a concern-or-question / a
    changes_requested review event / any human comment at all. Truly spontaneous = NONE of
    those precede it.

  CODE comments (agent-authored SATD): classify by commit phase —
    first_attempt = before any review (spontaneous); revision = after review (elicited);
    human_applied = the commit was a human's, not the agent's (excluded, not self-admission).

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

ADMIT = re.compile(r"\b(you'?re (absolutely )?right|you are right|you'?re correct|good catch|"
    r"my (mistake|bad|error|fault)|i missed|i overlooked|i forgot|i made (a|an) (mistake|error)|"
    r"i was wrong|my oversight|an oversight|apolog(y|ies|ize)|i incorrectly|i should have|"
    r"thanks for catching|that'?s (a|my) (mistake|bug|oversight)|i didn'?t (handle|account|notice))\b", re.I)
ERR = re.compile(r"\b(doesn'?t work|does not work|not working|won'?t work|broken|breaks?\b|fail(s|ed|ing)?|"
    r"\berror\b|\bbug\b|buggy|\bwrong\b|incorrect|crash\w*|throws?\b|exception|null ?pointer|\bnpe\b|"
    r"not right|unexpected|regression|doesn'?t compile|won'?t compile|test(s)? fail|isn'?t working|"
    r"shouldn'?t\b|this is broken|\btypo\b|not correct|misses)\b", re.I)
# broad: developer raising ANYTHING (question, suggestion, doubt) that could prompt the agent
CONCERN = re.compile(r"\?|\b(why|what|how come|hmm+|weird|odd|strange|confus\w*|unclear|not sure|"
    r"doesn'?t seem|looks? (off|wrong|weird|strange)|suspicious|should (we|this|it|you|be)|"
    r"could you|can you|would you|please|consider|not quite|are you sure|i think|i'?d prefer|"
    r"instead of|rather than|needs? to|expected|remove this|change this|revert)\b", re.I)


def has_debt(t):
    return sd.detect_conversational(t) is not None


def run():
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    conn.row_factory = sqlite3.Row

    agent_prs = set()
    for r in conn.execute("SELECT DISTINCT pr_id, actor FROM review_events WHERE body IS NOT NULL"):
        if agents.classify_actor(r["actor"])[0] == "coding_agent":
            agent_prs.add(r["pr_id"])

    conv = Counter()
    rows = []
    for pr in agent_prs:
        evs = [dict(r) for r in conn.execute(
            "SELECT e.actor actor, e.body body, e.kind kind, p.repo repo, p.pr_number prn, p.author author "
            "FROM review_events e JOIN prs p ON p.id=e.pr_id "
            "WHERE e.pr_id=? AND e.body IS NOT NULL ORDER BY e.ts, e.id", (pr,))]
        if not evs:
            continue
        author = evs[0]["author"]
        if agents.classify_actor(author)[0] != "coding_agent":
            continue
        tl = [(agents.classify_actor(e["actor"])[0], sd.strip_code(e["body"] or ""), e["actor"],
               e["kind"], e["repo"], e["prn"]) for e in evs]
        for i, (kind, txt, actor, rawkind, repo, prn) in enumerate(tl):
            if actor != author:
                continue                                       # authoring agent only
            if not (has_debt(txt) or ADMIT.search(txt)):
                continue                                       # agent expresses debt or admits a mistake
            # scan ALL preceding turns for any developer signal
            pdebt = perr = pconcern = pcr = phuman = False
            for j in range(i):
                kj, tj, aj, rkj = tl[j][0], tl[j][1], tl[j][2], tl[j][3]
                if rkj == "changes_requested":
                    pcr = True
                if kj == "human":
                    phuman = True
                    if has_debt(tj): pdebt = True
                    if ERR.search(tj): perr = True
                    if CONCERN.search(tj): pconcern = True
            if pdebt:      trig = "dev_named_debt"
            elif perr:     trig = "dev_alluded_error"
            elif pcr:      trig = "changes_requested"
            elif pconcern: trig = "dev_raised_concern/question"
            elif phuman:   trig = "neutral_dev_comment_before"
            else:          trig = "SPONTANEOUS (no dev signal)"
            conv[trig] += 1
            rows.append({"repo": repo, "pr": prn, "agent": actor, "trigger": trig,
                         "agent_text": txt[:250]})

    # --- CODE comments: phase-based trigger ---
    code = Counter()
    for r in conn.execute("""SELECT COALESCE(cm.phase,'no_commit') ph, COUNT(*) n
        FROM satd_instances i LEFT JOIN commits cm ON cm.sha=i.introducing_sha
        WHERE i.channel='code_comment' AND i.introducer_kind='coding_agent' GROUP BY ph"""):
        code[r["ph"]] = r["n"]

    pct = lambda n, d: f"{100*n/d:.1f}%" if d else "n/a"
    print("=" * 68)
    print("  WHAT TRIGGERS AN AGENT'S DEBT ADMISSION (full-context, author-only)")
    print("=" * 68)
    tot_conv = sum(conv.values())
    print(f"\nCONVERSATIONAL agent debt/mistake expressions: {tot_conv}")
    for k, v in conv.most_common():
        print(f"  {k:32} {v:5}  ({pct(v, tot_conv)})")
    spont_conv = conv.get("SPONTANEOUS (no dev signal)", 0)
    elicited_conv = tot_conv - spont_conv
    print(f"  --> elicited (any dev signal): {elicited_conv} ({pct(elicited_conv,tot_conv)})   "
          f"truly spontaneous: {spont_conv} ({pct(spont_conv,tot_conv)})")

    print(f"\nCODE-COMMENT SATD (agent-introduced), by commit phase:")
    tot_code = sum(code.values())
    fa = code.get("first_attempt", 0); rev = code.get("revision", 0); ha = code.get("human_applied", 0)
    print(f"  first_attempt (before review = SPONTANEOUS): {fa}  ({pct(fa,tot_code)})")
    print(f"  revision (after review = ELICITED):          {rev}  ({pct(rev,tot_code)})")
    print(f"  human_applied (not the agent's commit):      {ha}  ({pct(ha,tot_code)})")

    print(f"\n>>> TRULY SPONTANEOUS debt admissions (no developer signal at all):")
    print(f"      conversational: {spont_conv}")
    print(f"      code (first_attempt): {fa}")
    print(f"      TOTAL spontaneous: {spont_conv + fa}")
    print(f"    everything else was elicited by / followed a developer signal.")

    out = HERE / "agent_satd_triggers.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "pr", "agent", "trigger", "agent_text"])
        w.writeheader(); w.writerows(rows)
    print(f"\nconversational rows with trigger logged: {len(rows)} -> {out.name}")
    conn.close()


if __name__ == "__main__":
    run()
