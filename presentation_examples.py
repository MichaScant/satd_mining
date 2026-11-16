"""Presentation-ready examples. Writes presentation_examples.md and prints.

  A. Full case studies — for each: the debt the AI introduced (+ code), the developer
     conversation around it, the fix the AI made (+ code), and the convo leading to the fix.
  B. AI admitting its own debt, by concern category, split ELICITED (from a dev convo) vs
     SPONTANEOUS (on its own).

  python3 presentation_examples.py
"""
import csv
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agents as ag
import satd_detect as sd

c = sqlite3.connect(HERE / "db" / "satd_mining.db"); c.row_factory = sqlite3.Row
BASE = "channel='code_comment' AND introducer_kind='coding_agent'"
SP = "SPONTANEOUS (no dev signal)"
out = []
def emit(s=""): print(s); out.append(s)


def _h(t, *ws): return any(w in t for w in ws)
def concern(t):
    t = (t or "").lower()
    if _h(t, "don't know", "not sure", "no idea", "unclear"): return "uncertainty / unclear"
    if _h(t, "silly", "ugly", "stupid", "messy", "crap", "nasty", "hacky"): return "self-judged poor quality"
    if _h(t, "will break", "is broken", "fragile"): return "fragility / will break"
    if _h(t, "workaround", "work around", "hack", "kludge"): return "workaround / hack"
    if _h(t, "not supported", "not implemented", "not yet", "unsupported", "only support"): return "unsupported / not implemented"
    if _h(t, "placeholder", "stub", "dummy"): return "placeholder / stub"
    if _h(t, "remove this", "should be removed", "get rid", "revisit", "eventually"): return "flagged to remove / revisit"
    if _h(t, "for now", "temporar", "temp ", "quick fix"): return "interim / stopgap"
    return "other"


def norm(t, n=220): return " ".join((t or "").split())[:n]
def hunk(h, n=12):
    lines = (h or "").splitlines()
    return "\n".join("    " + l for l in lines[:n]) + ("\n    …" if len(lines) > n else "")


def pr_id(repo, prnum):
    r = c.execute("SELECT id FROM prs WHERE repo=? AND pr_number=?", (repo, prnum)).fetchone()
    return r["id"] if r else None


def convo(repo, prnum, author, max_turns=8):
    """Readable dev<->AI turns for a PR (skips automation; trims text)."""
    pid = pr_id(repo, prnum)
    if not pid:
        return []
    turns = []
    for e in c.execute("SELECT actor, body, kind FROM review_events WHERE pr_id=? AND body IS NOT NULL ORDER BY ts, id", (pid,)):
        k = ag.classify_actor(e["actor"])[0]
        if k not in ("human", "coding_agent"):
            continue
        who = "DEV " if k == "human" else "AI  "
        txt = norm(sd.strip_code(e["body"] or ""))
        if len(txt) < 8:
            continue
        turns.append(f"  {who} {e['actor']}: {txt}")
    return turns[:max_turns]


# ============================= A. CASE STUDIES =============================
emit("=" * 80); emit("A. CASE STUDIES — debt → developer conversation → the fix"); emit("=" * 80)
_raw = c.execute(f"""
  SELECT repo, introducing_pr, resolved_pr, path, resolved_path, comment_text_raw ct,
         intro_hunk, fix_hunk, circumstance, n_ai_file_edits fe, n_ai_near_edits ne, agent_name ag
  FROM satd_instances WHERE {BASE} AND status IN ('resolved','self_resolved_premerge')
    AND intro_hunk IS NOT NULL AND fix_hunk IS NOT NULL
    AND circumstance IN ('dev named debt','dev alluded to error','changes_requested')
  ORDER BY (n_ai_file_edits>0) DESC, n_ai_near_edits DESC LIMIT 60""").fetchall()
cases, seen = [], set()
for x in _raw:                                    # one case per introducing PR
    if x["introducing_pr"] in seen:
        continue
    seen.add(x["introducing_pr"]); cases.append(x)
    if len(cases) >= 3:
        break

for i, x in enumerate(cases, 1):
    author = c.execute("SELECT author FROM prs WHERE repo=? AND pr_number=?", (x["repo"], x["introducing_pr"])).fetchone()
    author = author["author"] if author else None
    emit(f"\n{'-'*80}\nCASE {i}: [{x['ag']}] {x['repo']}  (PR #{x['introducing_pr']} → fixed in #{x['resolved_pr']})")
    emit(f"circumstance: {x['circumstance']}   |   AI file-edits before fix: {x['fe']}  (near the debt: {x['ne']})")
    emit(f"\n  ① DEBT THE AI INTRODUCED  —  {x['path']}")
    emit(f"     comment: \"{norm(x['ct'],160)}\"")
    emit("     code:"); emit(hunk(x["intro_hunk"]))
    emit(f"\n  ② DEVELOPER CONVERSATION (introducing PR #{x['introducing_pr']}):")
    for t in convo(x["repo"], x["introducing_pr"], author):
        emit(t)
    emit(f"\n  ③ THE FIX (PR #{x['resolved_pr']}  —  {x['resolved_path'] or x['path']}):")
    emit(hunk(x["fix_hunk"]))
    if x["resolved_pr"] and x["resolved_pr"] != x["introducing_pr"]:
        emit(f"\n  ④ CONVERSATION LEADING TO THE FIX (PR #{x['resolved_pr']}):")
        for t in convo(x["repo"], x["resolved_pr"], None):
            emit(t)

# ============================= B. ADMISSIONS BY CATEGORY =============================
emit("\n\n" + "=" * 80)
emit("B. AI ADMITTING ITS OWN DEBT — by concern category, ELICITED vs SPONTANEOUS")
emit("=" * 80)
CATS = ["interim / stopgap", "placeholder / stub", "unsupported / not implemented",
        "workaround / hack", "flagged to remove / revisit", "self-judged poor quality",
        "uncertainty / unclear", "fragility / will break"]
# bucket code-comment admissions by category x elicited/spontaneous
buckets = {cat: {"spont": [], "elic": []} for cat in CATS}
for r in c.execute(f"SELECT repo, introducing_pr, path, comment_text_raw ct, circumstance ci, agent_name ag "
                   f"FROM satd_instances WHERE {BASE} AND comment_text_raw IS NOT NULL"):
    cat = concern(r["ct"])
    if cat not in buckets:
        continue
    key = "spont" if r["ci"] == SP else ("elic" if r["ci"] and "dev" in r["ci"] else None)
    if key and len(buckets[cat][key]) < 3 and len(norm(r["ct"])) > 30:
        buckets[cat][key].append(f"[{r['ag']}] {r['repo']}#{r['introducing_pr']}: \"{norm(r['ct'],150)}\"")

for cat in CATS:
    emit(f"\n### {cat} ###")
    emit("  ON ITS OWN (spontaneous):")
    for e in buckets[cat]["spont"][:2] or ["    (none found)"]:
        emit("    " + e)
    emit("  FROM A DEV CONVERSATION (elicited):")
    for e in buckets[cat]["elic"][:2] or ["    (none found)"]:
        emit("    " + e)

(HERE / "presentation_examples.md").write_text("\n".join(out))
emit(f"\n-> saved presentation_examples.md ({len(out)} lines)")
c.close()
