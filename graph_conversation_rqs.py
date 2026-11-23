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
        ax.bar(x - w/2, [res[a][b] for b in order], w, label="resolved", color="#3bb08f")
        ax.bar(x + w/2, [ali[a][b] for b in order], w, label="alive", color="#8a8a8a")
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
    # conversation self-admissions grouped by ELICITATION (code first_pointer is degenerate: a code
    # SATD exists before any review, so it is always 'ai'-first). Resolution rate = stage5b conv_fixed;
    # time = the linked code SATD's survival_days (same repo/pr/file) where it resolved.
    import statistics as stt
    HUM = {"dev named debt", "dev alluded to error", "dev changes_requested",
           "dev raised concern/question", "neutral dev comment before"}
    REV = {"ai_reviewer named debt", "ai_reviewer raised concern", "neutral ai_reviewer comment before"}
    rows = c.execute(
        "SELECT i.circumstance circ, i.status st, "
        " (SELECT cs.survival_days FROM satd_instances cs WHERE cs.repo=i.repo "
        "  AND cs.introducing_pr=i.introducing_pr AND cs.path=i.path AND cs.channel='code_comment' "
        "  AND cs.survival_days IS NOT NULL LIMIT 1) surv "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='conversation' AND i.source_kind='agent_reply' "
        "AND i.circumstance IS NOT NULL").fetchall()
    grp = {"spontaneous": [], "human-elicited": [], "ai_reviewer-elicited": []}
    for r in rows:
        b = "human-elicited" if r["circ"] in HUM else ("ai_reviewer-elicited" if r["circ"] in REV else "spontaneous")
        grp[b].append((r["st"] == "conv_fixed", r["surv"]))
    print("\n[AI-spotted-without-dev -> resolved sooner/more? (conversation elicitation)]")
    labels, rates, med = [], [], []
    for k in ("spontaneous", "ai_reviewer-elicited", "human-elicited"):
        g = grp[k]
        if not g:
            continue
        rate = 100 * sum(1 for fx, _ in g if fx) / len(g)
        times = [s for _, s in g if s is not None]
        m = stt.median(times) if times else float("nan")
        labels.append(f"{k}\n(n={len(g)})"); rates.append(rate); med.append(m)
        print(f"   {k:22} n={len(g):5}  conv_fixed={rate:.1f}%  "
              f"median linked-code days-to-resolve={m:.1f}" if times
              else f"   {k:22} n={len(g):5}  conv_fixed={rate:.1f}%  (no linked resolved times)")
    if not labels:
        print("  [who-spotted] no conversation circumstance — run analyze_reviewer_elicited_satd.py first; skipping")
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.5))
    cols = ["#3bb08f", "#c05a8f", "#e07b39"][:len(labels)]
    a1.bar(labels, rates, color=cols); a1.set_ylabel("% conv_fixed"); a1.set_title("Resolution rate by how it was spotted")
    a2.bar(labels, med, color=cols); a2.set_ylabel("median days to resolve (linked code)")
    a2.set_title("Time-to-resolve by how it was spotted")
    fig.suptitle("Does AI-spotted debt (spontaneous / AI-reviewer, no developer) resolve sooner or more often?")
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
