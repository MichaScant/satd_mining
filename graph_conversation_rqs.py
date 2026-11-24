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
    # AGENT-spotted SATD by HOW it was spotted, from the persisted how_spotted (spontaneous /
    # dev_elicited / reviewbot_elicited — includes the PR-author=dev-initiated signal). All channels.
    from stage1_aidev import AIDEV_AGENT_MAP
    canon = lambda a: AIDEV_AGENT_MAP.get(str(a or "").strip().lower(), str(a))
    rows = c.execute(
        "SELECT i.agent_name an, p.agent pag, i.how_spotted hw FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.spotter='agent' AND i.how_spotted IS NOT NULL").fetchall()
    data = defaultdict(Counter)
    for r in rows:
        ag = r["an"] if r["an"] else canon(r["pag"])
        data[ag][r["hw"]] += 1
    agents = [a for a in _agents_present(c) if a in data]
    if not agents:
        print("  [elicitation] no how_spotted data — run persist_spotter.py; skipping"); return
    keys = ("spontaneous", "dev_elicited", "reviewbot_elicited")
    cmap = {"spontaneous": "#3bb08f", "dev_elicited": "#e07b39", "reviewbot_elicited": "#c05a8f"}
    print("\n[how agent spotted its debt, per agent]")
    import numpy as np
    fig, ax = plt.subplots(figsize=(max(7, 1.3 * len(agents)), 5))
    x = np.arange(len(agents)); bottom = np.zeros(len(agents))
    for k in keys:
        vals = np.array([data[a][k] for a in agents], float)
        ax.bar(x, vals, 0.6, bottom=bottom, label=k.replace("_", "-"), color=cmap[k])
        for xi, (v, b0) in enumerate(zip(vals, bottom)):
            if v > 0:                                  # count label centred in each segment
                ax.text(xi, b0 + v / 2, f"{int(v)}", ha="center", va="center",
                        color="white", fontsize=8, fontweight="bold")
        bottom += vals
    for xi, a in enumerate(agents):
        t = sum(data[a].values())
        ax.text(xi, bottom[xi] + max(bottom) * 0.01, f"n={t}", ha="center", va="bottom", fontsize=8)
        print(f"   {a:8} n={t:5}  spont={data[a]['spontaneous']}  dev={data[a]['dev_elicited']}  "
              f"reviewbot={data[a]['reviewbot_elicited']}")
    ax.set_xticks(x); ax.set_xticklabels(agents); ax.set_ylabel("agent-spotted SATD")
    ax.set_title("How each agent spotted its debt: spontaneous vs dev-elicited vs reviewbot"); ax.legend()
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
    # resolved -> revisits strictly BEFORE the fix (n_revisits_pre_res, from the fetched commit dates);
    # alive -> total in-method revisits (n_ai_near_edits — there is no fix to bound against).
    # Split per authoring agent (the agent that introduced the debt) as small multiples.
    rows = c.execute(
        "SELECT i.agent_name ag, i.status st, i.n_ai_near_edits nr, i.n_revisits_pre_res npr "
        "FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment' AND i.n_ai_near_edits IS NOT NULL "
        "AND i.agent_name IS NOT NULL").fetchall()
    def bucket(n): return "0" if n == 0 else ("1" if n == 1 else ("2" if n == 2 else "3+"))
    res = defaultdict(Counter); ali = defaultdict(Counter)
    rs = defaultdict(int); al = defaultdict(int); rsum = defaultdict(int); asum = defaultdict(int)
    for r in rows:
        ag = r["ag"]
        if r["st"] in RESOLVED:
            n = r["npr"] if r["npr"] is not None else (r["nr"] or 0)   # before-resolution count
            res[ag][bucket(n)] += 1; rs[ag] += 1; rsum[ag] += n
        elif r["st"] == "alive":
            n = r["nr"] or 0
            ali[ag][bucket(n)] += 1; al[ag] += 1; asum[ag] += n
    agents = [a for a in _agents_present(c) if (rs[a] or al[a])]
    if not agents:
        print("  [revisits before resolution] no per-agent data — skipping"); return
    print("\n[revisits before resolution — AIDev code SATD, per authoring agent]")
    for a in agents:
        rmean = f"{rsum[a]/rs[a]:.2f}" if rs[a] else "—"
        amean = f"{asum[a]/al[a]:.2f}" if al[a] else "—"
        print(f"   {a:8} resolved n={rs[a]:5} mean-before-fix={rmean:>5}   "
              f"alive n={al[a]:5} mean-total={amean:>5}")
    order = ["0", "1", "2", "3+"]
    import numpy as np
    x = np.arange(len(order)); w = 0.4
    ncol = min(3, len(agents)); nrow = (len(agents) + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 3.4 * nrow), squeeze=False)
    for idx, a in enumerate(agents):
        ax = axes[idx // ncol][idx % ncol]
        rvals = [res[a][b] for b in order]; avals = [ali[a][b] for b in order]
        br = ax.bar(x - w/2, rvals, w, label="resolved", color="#3bb08f")
        ba = ax.bar(x + w/2, avals, w, label="alive", color="#8a8a8a")
        mx = max(rvals + avals + [1])
        for bar, v in list(zip(br, rvals)) + list(zip(ba, avals)):
            if v > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, v + mx * 0.02, str(v),
                        ha="center", va="bottom", fontsize=7)
        ax.set_ylim(0, mx * 1.18)
        ax.set_xticks(x); ax.set_xticklabels([f"{b}" for b in order])
        rmean = f"{rsum[a]/rs[a]:.2f}" if rs[a] else "—"
        ax.set_title(f"{a}  (resolved n={rs[a]}, μ-before-fix={rmean})", fontsize=10)
        ax.set_ylabel("SATD instances")
        if idx == 0:
            ax.legend(fontsize=8)
    for j in range(len(agents), nrow * ncol):          # hide unused panels
        axes[j // ncol][j % ncol].axis("off")
    fig.suptitle("In-method revisits before resolution, by authoring agent (AIDev)", fontsize=13)
    fig.supxlabel("in-method revisits")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(HERE / "rq_revisit_before_resolution.png", dpi=150); plt.close(fig)
    print("  saved rq_revisit_before_resolution.png")


# ---------------------------------------------------------------- 4. AI-spotted (no dev) -> resolved sooner/more?
def fig_who_spotted_resolution(c):
    # RQ2 (agent-spotted only): resolution rate + mean revisits by HOW the agent spotted the debt
    # (persisted how_spotted). res% over trackable (resolved+dropped); revisits = patch-based (code).
    rows = c.execute(
        "SELECT i.how_spotted hw, i.outcome oc, i.n_revisits_resolve nr FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.spotter='agent' AND i.how_spotted IS NOT NULL").fetchall()
    oc = defaultdict(Counter); revs = defaultdict(list)
    for r in rows:
        oc[r["hw"]][r["oc"]] += 1
        if r["oc"] == "resolved" and r["nr"] is not None:
            revs[r["hw"]].append(r["nr"])
    order = ["spontaneous", "dev_elicited", "reviewbot_elicited"]
    print("\n[RQ2: agent-spotted resolution by how-spotted]")
    labels, rates, mrev = [], [], []
    for k in order:
        res = oc[k]["resolved"]; dr = oc[k]["dropped"]; track = res + dr
        if not track:
            continue
        rate = 100 * res / track; mr = (sum(revs[k]) / len(revs[k])) if revs[k] else 0
        labels.append(f"{k.replace('_', '-')}\n(n={track})"); rates.append(rate); mrev.append(mr)
        print(f"   {k:20} resolved={res}  res%(track)={rate:.0f}%  mean_revisits={mr:.2f} (n={len(revs[k])})")
    if not labels:
        print("  [who-spotted] no how_spotted — run persist_spotter.py; skipping"); return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.5))
    cols = ["#3bb08f", "#e07b39", "#c05a8f"][:len(labels)]
    a1.bar(labels, rates, color=cols); a1.set_ylabel("resolution rate (%)")
    a1.set_title("Resolution rate by how the agent spotted it")
    a2.bar(labels, mrev, color=cols); a2.set_ylabel("mean revisits before fix")
    a2.set_title("Revisits to resolution by how spotted")
    fig.suptitle("RQ2: agent-spotted debt — does how it was spotted affect resolution?")
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
    b = ax.bar(agents, rates, color="#4682b4")
    for bar, a, r in zip(b, agents, rates):
        ax.text(bar.get_x() + bar.get_width() / 2, r + 0.4, f"{r:.1f}%\n{rev[a]}/{tot[a]}",
                ha="center", va="bottom", fontsize=8, fontweight="bold")
    ax.set_ylim(0, max(rates) * 1.25); ax.set_ylabel("% of code SATD revisited")
    ax.set_title("In-method revisit rate per agent (AIDev)  —  revisited / total code SATD")
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
