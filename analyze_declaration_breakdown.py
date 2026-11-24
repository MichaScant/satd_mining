"""Break the per-agent 'declared' SATD count down by HOW it was declared, and (if stage6 has run)
cross it with the resolution outcome.

How-declared categories (collapsing circumstance + source_kind):
  ai_spontaneous   AI left/admitted the debt with no external prompt
                   (circumstance SPONTANEOUS, or an unclassified PR-body admission).
  dev_prompted     debt surfaced with developer involvement — the dev named/alluded/requested it
                   (circumstance dev-*, or source_kind developer_request).
  aireviewer       an AI review bot surfaced it (circumstance ai_reviewer-*, or source ai_reviewer_flag).

Issues are NOT a declaration channel here (they are a resolution venue), so there is no "via issue"
bucket on the declared side.

  python analyze_declaration_breakdown.py
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import store
from stage1_aidev import AIDEV_AGENT_MAP

_DEV = {"requested_by_developer", "dev named debt", "dev alluded to error",
        "dev raised concern/question", "dev changes_requested", "neutral dev comment before"}
_REV = {"flagged_by_ai_reviewer", "ai_reviewer named debt", "ai_reviewer raised concern",
        "neutral ai_reviewer comment before"}


def _how(circ):
    if circ in _DEV:
        return "dev_prompted"
    if circ in _REV:
        return "aireviewer"
    return "ai_spontaneous"                 # SPONTANEOUS (no signal) or NULL (unclassified pr_body)


def run():
    conn = store.connect()
    canon = lambda a: AIDEV_AGENT_MAP.get(str(a or "").strip().lower(), str(a))
    has_outcome = any(r[1] == "outcome" for r in conn.execute("PRAGMA table_info(satd_instances)"))

    decl = defaultdict(Counter)                     # agent -> how -> count
    res = defaultdict(lambda: defaultdict(Counter)) # agent -> how -> outcome -> count
    oc_col = "i.outcome" if has_outcome else "NULL"

    # code_comment attributed by agent_name; conversation by the PR's agent
    for row in conn.execute(
        f"SELECT i.agent_name AS ag, i.circumstance AS circ, {oc_col} AS oc "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment'"):
        ag = row["ag"]; how = _how(row["circ"])
        decl[ag][how] += 1
        if has_outcome:
            res[ag][how][row["oc"]] += 1
    for row in conn.execute(
        f"SELECT p.agent AS ag, i.circumstance AS circ, {oc_col} AS oc "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='conversation'"):
        ag = canon(row["ag"]); how = _how(row["circ"])
        decl[ag][how] += 1
        if has_outcome:
            res[ag][how][row["oc"]] += 1

    order = ["ai_spontaneous", "dev_prompted", "aireviewer"]
    print("\n[declared SATD by HOW declared, per agent]")
    print(f"{'agent':8}{'declared':>10}{'spontaneous':>13}{'dev_prompt':>12}{'ai_reviewer':>13}")
    for ag in sorted(decl, key=lambda a: -sum(decl[a].values())):
        if ag in ("None", None):
            continue
        d = decl[ag]; tot = sum(d.values())
        print(f"{ag:8}{tot:>10}{d['ai_spontaneous']:>13}{d['dev_prompted']:>12}{d['aireviewer']:>13}")

    if has_outcome:
        print("\n[resolved count (res% of that declaration type) — how-declared x resolution]")
        print(f"{'agent':8}" + "".join(f"{h:>22}" for h in order))
        for ag in sorted(res, key=lambda a: -sum(decl[a].values())):
            if ag in ("None", None):
                continue
            cells = []
            for h in order:
                r = res[ag][h]["resolved"]
                t = r + res[ag][h]["dropped"]        # trackable denom (exclude 'excluded'), matches stage6
                cells.append(f"{r}/{t} ({100*r/t:.0f}%)" if t else "0/0")
            print(f"{ag:8}" + "".join(f"{c:>22}" for c in cells))
    else:
        print("\n(stage6 outcome not in this DB — run on the server DB for the resolution cross)")
    conn.close()


if __name__ == "__main__":
    run()
