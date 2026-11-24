"""RQ figures from the persisted spotter/how_spotted classification (run persist_spotter.py first).

  RQ1a  who spotted the SATD (agent / dev / reviewbot), per agent          -> graphs/rq1_who_spotted.png
  RQ1b  agent-spotted: HOW (spontaneous / dev-elicited / reviewbot-elic.)  -> graphs/rq1_how_agent_spotted.png
  RQ2a  agent-spotted resolution rate + mean revisits, by how-spotted      -> graphs/rq2_resolution_by_how.png
  RQ2b  agent-spotted outcome mix (resolved/dropped/excluded), per agent   -> graphs/rq2_outcome_by_agent.png

RQ2 is scoped to spotter='agent' (debt the AGENT spotted, dev-elicited or not), as specified.
Prints the underlying numbers too.
"""
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stage1_aidev import AIDEV_AGENT_MAP

DB = HERE / "db" / "satd_mining.db"
OUT = HERE / "graphs"; OUT.mkdir(exist_ok=True)
AGENTS = ["claude", "codex", "cursor", "devin", "jules"]
COL = {"agent": "#3bb08f", "dev": "#e07b39", "reviewbot": "#c05a8f",
       "spontaneous": "#3bb08f", "dev_elicited": "#e07b39", "reviewbot_elicited": "#c05a8f",
       "resolved": "#3bb08f", "dropped": "#8a8a8a", "excluded": "#d9c36b"}


def _canon(a):
    return AIDEV_AGENT_MAP.get(str(a or "").strip().lower(), str(a))


def _rows():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT i.agent_name an, p.agent pag, i.spotter sp, i.how_spotted hw, i.outcome oc, "
        "i.n_revisits_resolve nr FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr WHERE p.cohort='aidev'").fetchall()
    c.close()
    out = []
    for r in rows:
        ag = r["an"] if r["an"] else _canon(r["pag"])
        if ag in AGENTS:
            out.append((ag, r["sp"], r["hw"], r["oc"], r["nr"]))
    return out


def _stacked(ax, agents, data, keys, title, ylab):
    x = np.arange(len(agents)); bottom = np.zeros(len(agents))
    for k in keys:
        vals = np.array([data[a].get(k, 0) for a in agents], float)
        ax.bar(x, vals, 0.62, bottom=bottom, label=k.replace("_", "-"), color=COL[k])
        bottom += vals
    ax.set_xticks(x); ax.set_xticklabels(agents); ax.set_title(title); ax.set_ylabel(ylab)
    ax.legend(fontsize=8)


def main():
    rows = _rows()

    # RQ1a — who spotted, per agent
    who = defaultdict(Counter)
    for ag, sp, hw, oc, nr in rows:
        who[ag][sp] += 1
    print("\n[RQ1a who spotted]")
    for a in AGENTS:
        t = sum(who[a].values())
        print(f"  {a:8} agent={who[a]['agent']} dev={who[a]['dev']} reviewbot={who[a]['reviewbot']} (n={t})")
    fig, ax = plt.subplots(figsize=(8, 4.8))
    _stacked(ax, AGENTS, who, ["agent", "dev", "reviewbot"],
             "RQ1: who spotted the SATD (all gathered)", "SATD instances")
    fig.tight_layout(); fig.savefig(OUT / "rq1_who_spotted.png", dpi=150); plt.close(fig)
    print("  saved graphs/rq1_who_spotted.png")

    # RQ1b — agent-spotted, how
    how = defaultdict(Counter)
    for ag, sp, hw, oc, nr in rows:
        if sp == "agent" and hw:
            how[ag][hw] += 1
    print("\n[RQ1b how the agent spotted it]")
    for a in AGENTS:
        t = sum(how[a].values()) or 1
        print(f"  {a:8} spont={how[a]['spontaneous']} dev={how[a]['dev_elicited']} "
              f"reviewbot={how[a]['reviewbot_elicited']}")
    fig, ax = plt.subplots(figsize=(8, 4.8))
    _stacked(ax, AGENTS, how, ["spontaneous", "dev_elicited", "reviewbot_elicited"],
             "RQ1: how the AGENT spotted its debt (agent-spotted only)", "agent-spotted SATD")
    fig.tight_layout(); fig.savefig(OUT / "rq1_how_agent_spotted.png", dpi=150); plt.close(fig)
    print("  saved graphs/rq1_how_agent_spotted.png")

    # RQ2a — agent-spotted resolution rate + mean revisits, by how-spotted
    RES = "resolved"
    byhow = defaultdict(lambda: Counter()); revs = defaultdict(list)
    for ag, sp, hw, oc, nr in rows:
        if sp != "agent" or not hw:
            continue
        byhow[hw][oc] += 1
        if oc == RES and nr is not None:
            revs[hw].append(nr)
    order = ["spontaneous", "dev_elicited", "reviewbot_elicited"]
    print("\n[RQ2a agent-spotted resolution by how-spotted]")
    resrate = {}; meanrev = {}
    for h in order:
        res = byhow[h]["resolved"]; dr = byhow[h]["dropped"]; track = res + dr
        resrate[h] = 100 * res / track if track else 0
        meanrev[h] = (sum(revs[h]) / len(revs[h])) if revs[h] else 0
        print(f"  {h:18} resolved={res} res%(track)={resrate[h]:.0f}% mean_revisits={meanrev[h]:.2f} (n={len(revs[h])})")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.5))
    xl = [h.replace("_", "-") for h in order]
    a1.bar(xl, [resrate[h] for h in order], color=[COL[h] for h in order])
    a1.set_ylabel("resolution rate (%)"); a1.set_title("RQ2: resolution rate by how-spotted (agent-spotted)")
    for i, h in enumerate(order):
        a1.text(i, resrate[h] + 0.5, f"{resrate[h]:.0f}%", ha="center", fontsize=9)
    a2.bar(xl, [meanrev[h] for h in order], color=[COL[h] for h in order])
    a2.set_ylabel("mean revisits before fix"); a2.set_title("RQ2: revisits to resolution by how-spotted")
    for i, h in enumerate(order):
        a2.text(i, meanrev[h] + 0.01, f"{meanrev[h]:.2f}", ha="center", fontsize=9)
    fig.tight_layout(); fig.savefig(OUT / "rq2_resolution_by_how.png", dpi=150); plt.close(fig)
    print("  saved graphs/rq2_resolution_by_how.png")

    # RQ2b — agent-spotted outcome mix per agent
    oc_by = defaultdict(Counter)
    for ag, sp, hw, oc, nr in rows:
        if sp == "agent":
            oc_by[ag][oc] += 1
    print("\n[RQ2b agent-spotted outcome mix per agent]")
    for a in AGENTS:
        print(f"  {a:8} resolved={oc_by[a]['resolved']} dropped={oc_by[a]['dropped']} excluded={oc_by[a]['excluded']}")
    fig, ax = plt.subplots(figsize=(8, 4.8))
    _stacked(ax, AGENTS, oc_by, ["resolved", "dropped", "excluded"],
             "RQ2: outcome of AGENT-spotted debt, per agent", "agent-spotted SATD")
    fig.tight_layout(); fig.savefig(OUT / "rq2_outcome_by_agent.png", dpi=150); plt.close(fig)
    print("  saved graphs/rq2_outcome_by_agent.png")


if __name__ == "__main__":
    main()
