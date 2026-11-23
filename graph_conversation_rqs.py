"""RQ graphs for the conversation + revisit analysis — per authoring agent, with AI-reviewer broken
out by tool. Prints the underlying numbers too (so results stand even if a plot looks off).

Figures (PNG, this dir):
  rq_elicitation_by_agent.png    noticing: spontaneous / human-elicited / ai_reviewer-elicited, per agent
  rq_aireviewer_by_tool.png      ai_reviewer debt flags by review TOOL (CodeRabbit / Copilot / Gemini / …)
  rq_revisit_before_resolution.png  revisit count (n_ai_near_edits) distribution: resolved vs alive
  rq_who_spotted_resolution.png  resolution rate + median time-to-resolve, by who pointed it out first
  rq_revisit_rate_by_agent.png   share of each agent's code SATD revisited (edited, not necessarily resolved)

  python graph_conversation_rqs.py
"""
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reviewer_tools

DB = HERE / "db" / "satd_mining.db"
RESOLVED = ("resolved", "file_deleted", "comment_deleted_only", "self_resolved_premerge")
AGENTS_ORDER = ["claude", "codex", "cursor", "devin", "jules", "copilot"]
COL = {"spontaneous": "#3bb08f", "human": "#e07b39", "ai_reviewer": "#c05a8f"}


def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c


def _agents_present(c):
    got = [r[0] for r in c.execute(
        "SELECT DISTINCT agent_name FROM satd_instances WHERE agent_name IS NOT NULL")]
    return [a for a in AGENTS_ORDER if a in got] + [a for a in got if a not in AGENTS_ORDER]


# ---------------------------------------------------------------- 1. elicitation per agent
def fig_elicitation_by_agent(c):
    # per-agent conversation elicitation comes from agent_notice_revisit.csv (not the DB), act=notice
    import csv
    HUM = {"dev named debt", "dev alluded to error", "dev changes_requested",
           "dev raised concern/question", "neutral dev comment before", "requested_by_developer"}
    REV = {"ai_reviewer named debt", "ai_reviewer raised concern", "neutral ai_reviewer comment before"}
    path = HERE / "agent_notice_revisit.csv"
    if not path.exists():
        print("  [elicitation] agent_notice_revisit.csv missing (pull it from the server) — skipping"); return
    data = defaultdict(lambda: Counter())
    with open(path) as f:
        for row in csv.DictReader(f):
            if row.get("act") != "notice" or not row.get("agent"):
                continue
            t = row.get("trigger", "")
            b = "human" if t in HUM else ("ai_reviewer" if t in REV else "spontaneous")
            data[row["agent"]][b] += 1
    agents = [a for a in _agents_present(c) if a in data]
    if not agents:
        print("  [elicitation] no per-agent conversation circumstance data — skipping"); return
    print("\n[elicitation per agent] (spontaneous / human / ai_reviewer)")
    fig, ax = plt.subplots(figsize=(max(7, 1.3 * len(agents)), 5))
    import numpy as np
    x = np.arange(len(agents)); bottom = np.zeros(len(agents))
    for b in ("spontaneous", "human", "ai_reviewer"):
        vals = [data[a][b] for a in agents]
        ax.bar(x, vals, 0.6, bottom=bottom, label=b, color=COL[b])
        bottom += vals
    for a in agents:
        t = sum(data[a].values())
        print(f"   {a:8} n={t:5}  spont={data[a]['spontaneous']}  human={data[a]['human']}  ai_rev={data[a]['ai_reviewer']}")
    ax.set_xticks(x); ax.set_xticklabels(agents); ax.set_ylabel("conversation admissions")
    ax.set_title("How each agent noticed its debt: spontaneous vs elicited"); ax.legend()
    fig.tight_layout(); fig.savefig(HERE / "rq_elicitation_by_agent.png", dpi=150); plt.close(fig)
    print("  saved rq_elicitation_by_agent.png")


# ---------------------------------------------------------------- 2. ai_reviewer flags by tool
def fig_aireviewer_by_tool(c):
    rows = c.execute(
        "SELECT e.actor actor, COUNT(*) n FROM satd_instances i "
        "JOIN review_events e ON e.id=i.utterance_id "
        "WHERE i.channel='conversation' AND i.source_kind='ai_reviewer_flag' AND e.actor IS NOT NULL "
        "GROUP BY e.actor").fetchall()
    tool = Counter()
    for r in rows:
        tool[reviewer_tools.reviewer_tool(r["actor"])[0]] += r["n"]
    if not tool:
        print("  [ai_reviewer by tool] none — skipping"); return
    print("\n[ai_reviewer debt flags by tool]")
    items = tool.most_common()
    for t, n in items:
        print(f"   {t:24} {n}")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh([t for t, _ in items][::-1], [n for _, n in items][::-1], color="#c05a8f")
    ax.set_xlabel("debt flags"); ax.set_title("AI-reviewer debt flags by tool")
    fig.tight_layout(); fig.savefig(HERE / "rq_aireviewer_by_tool.png", dpi=150); plt.close(fig)
    print("  saved rq_aireviewer_by_tool.png")


# ---------------------------------------------------------------- 3. revisits before resolution
def fig_revisit_before_resolution(c):
    rows = c.execute(
        "SELECT i.status st, i.n_ai_near_edits nr FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment' AND i.n_ai_near_edits IS NOT NULL").fetchall()
    def bucket(n): return "0" if n == 0 else ("1" if n == 1 else ("2" if n == 2 else "3+"))
    res = Counter(); ali = Counter()
    rs = al = 0; rsum = asum = 0
    for r in rows:
        nr = r["nr"] or 0
        if r["st"] in RESOLVED:
            res[bucket(nr)] += 1; rs += 1; rsum += nr
        elif r["st"] == "alive":
            ali[bucket(nr)] += 1; al += 1; asum += nr
    print("\n[revisits before resolution — AIDev code SATD]")
    print(f"   resolved: n={rs}  mean revisits={rsum/rs:.2f}" if rs else "   resolved: n=0")
    print(f"   alive:    n={al}  mean revisits={asum/al:.2f}" if al else "   alive: n=0")
    order = ["0", "1", "2", "3+"]
    import numpy as np
    x = np.arange(len(order)); w = 0.4
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.bar(x - w/2, [res[b] for b in order], w, label="resolved", color="#3bb08f")
    ax.bar(x + w/2, [ali[b] for b in order], w, label="alive", color="#8a8a8a")
    ax.set_xticks(x); ax.set_xticklabels([f"{b} revisits" for b in order])
    ax.set_ylabel("SATD instances"); ax.set_title("In-method revisits before resolution (AIDev)"); ax.legend()
    fig.tight_layout(); fig.savefig(HERE / "rq_revisit_before_resolution.png", dpi=150); plt.close(fig)
    print("  saved rq_revisit_before_resolution.png")


# ---------------------------------------------------------------- 4. who-spotted -> resolved sooner?
def fig_who_spotted_resolution(c):
    # AIDev merged code SATD with a first-pointer and a survival measurement
    rows = c.execute(
        "SELECT i.first_pointer_class fp, i.status st, i.survival_days sd FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment' AND i.first_pointer_class IS NOT NULL "
        "AND i.survival_days IS NOT NULL").fetchall()
    grp = {"ai": [], "ai_reviewer": [], "human": []}
    for r in rows:
        if r["fp"] in grp:
            grp[r["fp"]].append((r["st"] in RESOLVED, r["sd"]))
    print("\n[resolution by who pointed it out first — AIDev, merged]")
    labels, rates, med = [], [], []
    import statistics as stt
    for k in ("ai", "ai_reviewer", "human"):
        g = grp[k]
        if not g:
            continue
        rate = 100 * sum(1 for ok, _ in g if ok) / len(g)
        times = [sd for ok, sd in g if ok and sd is not None]
        m = stt.median(times) if times else float("nan")
        labels.append(f"{k}\n(n={len(g)})"); rates.append(rate); med.append(m)
        print(f"   {k:11} n={len(g):5}  resolved={rate:.1f}%   median days-to-resolve={m:.1f}" if times
              else f"   {k:11} n={len(g):5}  resolved={rate:.1f}%   (no resolved times)")
    if not labels:
        print("  [who-spotted] no data — skipping"); return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.5))
    a1.bar(labels, rates, color=["#3bb08f", "#c05a8f", "#e07b39"][:len(labels)])
    a1.set_ylabel("% resolved"); a1.set_title("Resolution rate by who spotted it")
    a2.bar(labels, med, color=["#3bb08f", "#c05a8f", "#e07b39"][:len(labels)])
    a2.set_ylabel("median days to resolve"); a2.set_title("Time-to-resolve by who spotted it")
    fig.suptitle("Does AI-spotted debt (no developer) resolve sooner?")
    fig.tight_layout(); fig.savefig(HERE / "rq_who_spotted_resolution.png", dpi=150); plt.close(fig)
    print("  saved rq_who_spotted_resolution.png")


# ---------------------------------------------------------------- 5. revisit rate per agent
def fig_revisit_rate_by_agent(c):
    rows = c.execute(
        "SELECT COALESCE(i.agent_name,p.agent) ag, i.n_ai_near_edits nr FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment' AND i.n_ai_near_edits IS NOT NULL").fetchall()
    tot = Counter(); rev = Counter()
    for r in rows:
        if not r["ag"]:
            continue
        tot[r["ag"]] += 1
        if (r["nr"] or 0) > 0:
            rev[r["ag"]] += 1
    agents = [a for a in _agents_present(c) if tot[a]]
    if not agents:
        print("  [revisit rate] none — skipping"); return
    print("\n[revisit rate per agent — AIDev]")
    rates = []
    for a in agents:
        r = 100 * rev[a] / tot[a]; rates.append(r)
        print(f"   {a:8} {rev[a]}/{tot[a]} = {r:.1f}% revisited")
    fig, ax = plt.subplots(figsize=(max(7, 1.2 * len(agents)), 4.5))
    ax.bar(agents, rates, color="#4682b4")
    ax.set_ylabel("% of code SATD revisited"); ax.set_title("In-method revisit rate per agent (AIDev)")
    fig.tight_layout(); fig.savefig(HERE / "rq_revisit_rate_by_agent.png", dpi=150); plt.close(fig)
    print("  saved rq_revisit_rate_by_agent.png")


def main():
    c = conn()
    fig_elicitation_by_agent(c)
    fig_aireviewer_by_tool(c)
    fig_revisit_before_resolution(c)
    fig_who_spotted_resolution(c)
    fig_revisit_rate_by_agent(c)
    c.close()


if __name__ == "__main__":
    main()
