"""The RQ hierarchy, in the exact structure we want:

  LEVEL 1  who spotted each SATD (of ALL gathered):  agent / developer / review-bot
  LEVEL 2  for AGENT-spotted only: how did the agent spot it — spontaneous / dev-elicited / reviewbot-elicited
  LEVEL 3  for agent-spotted that RESOLVED: how many revisits until resolution, by how-spotted

Review-bot and developer spotting are counted at Level 1 only (low detail, as agreed) — the depth is
reserved for the agent, since the question is whether the AGENT notices its own debt and what happens.

Spotter mapping:
  agent      = code_comment, or conversation agent_reply / pr_body   (genuine agent admission)
  developer  = conversation developer_request
  review-bot = conversation ai_reviewer_flag

Run AFTER reclassify_tool_posts + analyze + stage6 (needs circumstance, outcome, n_revisits_resolve).
  python analyze_spotter_hierarchy.py
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
from db import store
from stage1_aidev import AIDEV_AGENT_MAP

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
    return "agent"                              # agent_reply / pr_body


def _how(circ, author_kind):
    # reviewbot pointed at THIS debt -> reviewbot (debt-specific, highest priority)
    if circ in _REV:
        return "reviewbot_elicited"
    # a dev pointed at the debt in-thread, OR a human developer initiated/opened the PR (directed the
    # agent) -> dev-elicited. Per the agreed definition, a dev-created PR is not spontaneous.
    if circ in _DEV or author_kind == "human":
        return "dev_elicited"
    return "spontaneous"            # agent-bot PR, no dev signal -> genuinely autonomous


def run():
    conn = store.connect()
    canon = lambda a: AIDEV_AGENT_MAP.get(str(a or "").strip().lower(), str(a))

    spot = defaultdict(Counter)                                  # agent -> spotter -> n
    how = defaultdict(Counter)                                   # agent -> how -> n  (agent-spotted)
    rev = defaultdict(lambda: defaultdict(list))                 # agent -> how -> [revisits] (resolved)
    howres = defaultdict(lambda: defaultdict(Counter))           # agent -> how -> outcome -> n

    # code_comment: attributed by agent_name
    for r in conn.execute(
        "SELECT i.agent_name ag, i.channel ch, i.source_kind sk, i.circumstance circ, "
        "i.outcome oc, i.n_revisits_resolve nr, p.author author "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment'"):
        _tally(r, r["ag"], spot, how, rev, howres)
    # conversation: attributed by the PR's agent
    for r in conn.execute(
        "SELECT p.agent ag, i.channel ch, i.source_kind sk, i.circumstance circ, "
        "i.outcome oc, i.n_revisits_resolve nr, p.author author "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='conversation'"):
        _tally(r, canon(r["ag"]), spot, how, rev, howres)

    agents = [a for a in sorted(spot, key=lambda a: -sum(spot[a].values())) if a not in ("None", None)]

    print("\n=== LEVEL 1 — who spotted the SATD (all gathered) ===")
    print(f"{'agent':8}{'total':>9}{'agent':>9}{'dev':>8}{'reviewbot':>11}")
    tot = Counter()
    for a in agents:
        s = spot[a]; t = sum(s.values()); tot.update(s)
        print(f"{a:8}{t:>9}{s['agent']:>9}{s['dev']:>8}{s['reviewbot']:>11}")
    T = sum(tot.values())
    print(f"{'OVERALL':8}{T:>9}{tot['agent']:>9}{tot['dev']:>8}{tot['reviewbot']:>11}"
          f"   ({100*tot['agent']/T:.0f}% / {100*tot['dev']/T:.0f}% / {100*tot['reviewbot']/T:.0f}%)")

    print("\n=== LEVEL 2 — agent-spotted: HOW the agent spotted it ===")
    print(f"{'agent':8}{'agent_spotted':>15}{'spontaneous':>13}{'dev_elicited':>14}{'reviewbot_el':>14}")
    htot = Counter()
    for a in agents:
        h = how[a]; t = sum(h.values()); htot.update(h)
        print(f"{a:8}{t:>15}{h['spontaneous']:>13}{h['dev_elicited']:>14}{h['reviewbot_elicited']:>14}")
    HT = sum(htot.values())
    print(f"{'OVERALL':8}{HT:>15}{htot['spontaneous']:>13}{htot['dev_elicited']:>14}{htot['reviewbot_elicited']:>14}"
          f"   ({100*htot['spontaneous']/HT:.0f}% / {100*htot['dev_elicited']/HT:.0f}% / {100*htot['reviewbot_elicited']/HT:.0f}%)")

    print("\n=== LEVEL 3 — agent-spotted & RESOLVED: revisits to resolution, by how-spotted ===")
    print("    (mean_revisits = patch-based method near-edits before fix; code SATD only, n in parens)")
    print(f"{'how-spotted':16}{'resolved':>10}{'mean_revisits':>15}{'res%(track)':>13}")
    for h in ("spontaneous", "dev_elicited", "reviewbot_elicited"):
        revs = [x for a in agents for x in rev[a][h]]
        res = sum(howres[a][h]["resolved"] for a in agents)
        dr = sum(howres[a][h]["dropped"] for a in agents)
        track = res + dr
        mean = f"{sum(revs)/len(revs):.2f} (n={len(revs)})" if revs else "—"
        pct = f"{100*res/track:.0f}%" if track else "—"
        print(f"{h:16}{res:>10}{mean:>15}{pct:>13}")
    conn.close()


def _tally(r, agent, spot, how, rev, howres):
    sp = _spotter(r["ch"], r["sk"])
    spot[agent][sp] += 1
    if sp == "agent":
        author_kind = agents.classify_actor(r["author"] or "")[0]   # human dev vs agent bot
        h = _how(r["circ"], author_kind)
        how[agent][h] += 1
        howres[agent][h][r["oc"]] += 1
        if r["oc"] == "resolved" and r["nr"] is not None:   # patch-based revisits = code SATD only
            rev[agent][h].append(r["nr"])


if __name__ == "__main__":
    run()
