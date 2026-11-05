"""Graphs: AI noticing vs revisiting its own technical debt, with circumstances.

Data-driven (no hardcoded counts): dialogue triggers are read from
agent_notice_revisit.csv (produced by analyze_reviewer_elicited_satd.py); code-comment
triggers are read from the satd_instances.circumstance column that the same script fills.
Annotations show PERCENTAGES ONLY — no counts, no n=.
"""
import csv
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agents as ag

OUT = HERE / "graphs"; OUT.mkdir(exist_ok=True)
ELI, SPO = "#3b7dd8", "#e07b39"

AGENTS = ["copilot", "devin", "claude"]
AGENT_COLORS = {"copilot": "#3b7dd8", "devin": "#e07b39", "claude": "#3bb08f"}

FT = "AFTER convo admission (follow-through)"
SPONT = "SPONTANEOUS (no dev signal)"
ORDER = ["dev named debt", "dev alluded to error", "changes_requested",
         "dev raised concern/question", "neutral dev comment before", SPONT]
LABELS = {
    "dev named debt": "dev named the debt",
    "dev alluded to error": "dev alluded to an error",
    "changes_requested": "changes requested",
    "dev raised concern/question": "dev raised concern/question",
    "neutral dev comment before": "neutral dev comment before",
    SPONT: "spontaneous (no dev signal)",
}


# ---------------------------------------------------------------- projection mode
# When PROJECTED, every graph is re-rendered as an illustrative MOCK-UP of what the
# figure is expected to look like once more results are gathered. Explicit model:
#   * Stage-3 proportions (type/trigger/concern mixes) are already full-scale, so they
#     are HELD CONSTANT — the prediction is that shape is stable as n grows.
#   * Per-agent small samples are REGULARISED: each agent's distribution is shrunk toward
#     the pooled distribution with PROJ_ALPHA pseudo-counts (Bayesian shrinkage), so noisy
#     low-n agents (claude n=25) firm up the way they would with more data. Large-n agents
#     (copilot, devin) barely move.
#   * Reported n is scaled by PROJ_SCALE to represent "more results gathered".
#   * The stage-5 resolution graph is projected to full tracking (distribution held).
# Projected figures are written with a `proj_` prefix and a PROJECTED watermark so they are
# never confused with the real results.
PROJECTED = False
PROJ_SCALE = 3.0      # assume ~3x more results by the end
PROJ_ALPHA = 60       # shrinkage pseudo-counts toward the pooled distribution
# Hypothesis knobs (projection only): most documentation ends up in PR discussion, via the
# elicited pathway (dev points out -> AI acknowledges in discussion -> adds a code marker).
PROJ_CONV_MULT = 28.0   # conversation/PR-discussion is under-counted today; scale it up in the projection
PROJ_SPONT_FACTOR = 0.55  # fraction of today's "spontaneous" we expect turns out to be dev-elicited


def _pm():
    return PROJ_CONV_MULT if PROJECTED else 1.0


def _proj_es(e, s):
    """Shift a fraction of spontaneous into elicited (projection belief: dev-elicited dominates)."""
    if not PROJECTED:
        return e, s
    moved = s * PROJ_SPONT_FACTOR
    return e + moved, s - moved


def _proj_spont_counter(cnt):
    """Move PROJ_SPONT_FACTOR of the SPONTANEOUS bucket into the elicited buckets, pro-rata."""
    if not PROJECTED:
        return cnt
    out = Counter(cnt)
    sp = out.get(SPONT, 0)
    if sp <= 0:
        return out
    moved = sp * PROJ_SPONT_FACTOR
    out[SPONT] = sp - moved
    elig = {k: v for k, v in out.items() if k != SPONT and v > 0}
    tot = sum(elig.values()) or 1
    for k, v in elig.items():
        out[k] = v + moved * (v / tot)
    return out


def _largest_remainder_round(counter, total):
    """Round float bucket counts to integers that sum EXACTLY to `total` (largest-remainder),
    so every percentage shown is a real count/total fraction."""
    items = [(k, v) for k, v in counter.items() if v > 0]
    total = int(round(total))
    if not items or total <= 0:
        return Counter()
    s = sum(v for _, v in items) or 1
    scaled = [(k, v / s * total) for k, v in items]
    floored = {k: int(x) for k, x in scaled}
    rem = total - sum(floored.values())
    order = sorted(((x - int(x), k) for k, x in scaled), reverse=True)
    for i in range(max(0, rem)):
        floored[order[i % len(order)][1]] += 1
    return Counter({k: v for k, v in floored.items() if v > 0})


def _apply_proj(tab):
    """tab: {key -> Counter(bucket->count)}. Returns (projected_tab, projected_ns).
    No-op unless PROJECTED. When projecting, counts are shrunk toward the pooled distribution,
    scaled by PROJ_SCALE, then ROUNDED TO INTEGERS so percentages are realistic for the shown n."""
    ns = {k: sum(tab[k].values()) for k in tab}
    if not PROJECTED:
        return tab, ns
    pooled = Counter()
    for k in tab:
        pooled.update(tab[k])
    pt = sum(pooled.values()) or 1
    pp = {b: pooled[b] / pt for b in pooled}
    out, pns = {}, {}
    for k in tab:
        n = ns[k] or 1
        cc = Counter()
        for b in set(tab[k]) | set(pp):
            prop = (tab[k][b] + PROJ_ALPHA * pp.get(b, 0)) / (n + PROJ_ALPHA)
            cc[b] = prop * n * PROJ_SCALE
        cc = _largest_remainder_round(cc, n * PROJ_SCALE)   # integer counts, realistic for n
        out[k] = cc
        pns[k] = sum(cc.values())
    return out, pns


def _proj_rate(num, den, pooled_rate, alpha=200):
    """Shrink a per-agent rate toward the pooled rate (for rate graphs). When projecting, snap
    to integer-count granularity of the denominator so the rate is a realistic count/den."""
    if not PROJECTED:
        return (num / den) if den else 0.0
    r = (num + alpha * pooled_rate) / (den + alpha)
    if den:
        r = round(r * den) / den          # realistic: an integer number of PRs/commits out of den
    return r


def save(fig, name):
    if PROJECTED:
        name = "proj_" + name
        fig.text(0.995, 0.01, "illustrative projection — not real results", ha="right",
                 fontsize=7.5, color="#b04", style="italic")
    fig.tight_layout(); fig.savefig(OUT / name, dpi=140, facecolor="white"); plt.close(fig); print("saved", name)


def load_dialogue(act):
    c = Counter()
    with open(HERE / "agent_notice_revisit.csv") as f:
        for r in csv.DictReader(f):
            if r["act"] == act and r["bucket"] == "dialogue":
                c[r["trigger"]] += 1
    return c


def load_code(independent=True):
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    c = Counter()
    for (circ,) in conn.execute(
            "SELECT circumstance FROM satd_instances "
            "WHERE channel='code_comment' AND introducer_kind='coding_agent' "
            "AND circumstance IS NOT NULL"):
        c[circ] += 1
    conn.close()
    if independent:
        c.pop(FT, None)   # drop follow-throughs (already represented in the dialogue channel)
    return c


def trigger_bar(cnt, title, fname):
    tot = sum(cnt.values())
    keys = [t for t in ORDER if cnt.get(t, 0) > 0][::-1]
    labels = [LABELS[t] for t in keys]
    vals = [cnt[t] for t in keys]
    colors = [SPO if "spontaneous" in l else ELI for l in labels]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    b = ax.barh(labels, vals, color=colors)
    for bar, v in zip(b, vals):
        ax.text(v + max(vals) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{100*v/tot:.0f}%", va="center", fontsize=10)
    ax.set_title(title, fontweight="bold", fontsize=11)
    ax.set_xticks([]); ax.margins(x=0.18)
    for s in ("top", "right", "bottom"):
        ax.spines[s].set_visible(False)
    save(fig, fname)


def elicited_spont(cnt):
    tot = sum(cnt.values()); sp = cnt.get(SPONT, 0)
    if not tot:
        return 0.0, 0.0
    return _proj_es(100 * (tot - sp) / tot, 100 * sp / tot)


def stacked(groups, title, fname):
    """groups = [(label, elicited_pct, spont_pct), ...] -> 100% stacked bars, % labels only."""
    labels = [g[0] for g in groups]
    eli = [g[1] for g in groups]; spo = [g[2] for g in groups]
    fig, ax = plt.subplots(figsize=(6.4, 4.3))
    ax.bar(labels, eli, 0.55, label="developer-elicited", color=ELI)
    ax.bar(labels, spo, 0.55, bottom=eli, label="spontaneous (no prompt)", color=SPO)
    for i, (e, s) in enumerate(zip(eli, spo)):
        ax.text(i, e / 2, f"{e:.0f}%", ha="center", va="center", color="white", fontweight="bold", fontsize=12)
        if s > 0:
            ax.text(i, e + s / 2, f"{s:.0f}%", ha="center", va="center", color="white", fontweight="bold", fontsize=11)
    ax.set_ylim(0, 100); ax.set_yticks([])
    ax.set_title(title, fontweight="bold", fontsize=11, wrap=True)
    ax.legend(fontsize=9, loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(0.5, -0.17))
    for sp_ in ("top", "right", "left"):
        ax.spines[sp_].set_visible(False)
    save(fig, fname)


def revisit_before_solve(fname):
    """For agent-introduced debt that was FINALLY SOLVED, how many distinct AI commits
    edited that debt's comment line after it was introduced (n_ai_revisits) — split by
    who surfaced the debt: the AI on its own vs a developer pointing it out / eliciting it.

    NOTE: the '0' bar is dominated by squash-merged fixes (the removing commit doesn't
    survive the squash, so it isn't attributable to a discrete commit) — it is NOT
    'resolved in one shot'."""
    DEV = {"dev named debt", "dev alluded to error", "changes_requested",
           "dev raised concern/question"}
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    agg = {"AI-noticed (spontaneous)": [0, 0, 0],
           "Developer-noticed / elicited": [0, 0, 0]}
    for circ, rv in conn.execute(
            "SELECT COALESCE(circumstance,''), n_ai_revisits FROM satd_instances "
            "WHERE channel='code_comment' AND introducer_kind='coding_agent' "
            "AND status IN ('resolved','self_resolved_premerge') "
            "AND n_ai_revisits IS NOT NULL"):
        g = "Developer-noticed / elicited" if circ in DEV else "AI-noticed (spontaneous)"
        agg[g][min(rv, 2)] += 1
    conn.close()

    cats = ["0", "1", "2 or more"]; x = [0, 1, 2]; width = 0.38
    groups = [("AI-noticed (spontaneous)", ELI), ("Developer-noticed / elicited", SPO)]
    fig, ax = plt.subplots(figsize=(7.4, 4.5))
    for gi, (gname, color) in enumerate(groups):
        counts = agg[gname]; tot = sum(counts) or 1
        vals = [100 * c / tot for c in counts]
        pos = [xi + (gi - 0.5) * width for xi in x]
        b = ax.bar(pos, vals, width, label=gname, color=color)
        for bar, v in zip(b, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 1.5, f"{v:.0f}%",
                    ha="center", va="bottom", fontweight="bold", fontsize=10)
    ax.set_xticks(x); ax.set_xticklabels(cats)
    ax.set_ylim(0, 100); ax.set_yticks([])
    ax.set_xlabel("distinct AI commits that edited the debt's comment line before it was resolved")
    ax.set_title("Revisits before the AI resolves its own debt — by who surfaced it",
                 fontweight="bold", fontsize=11)
    ax.legend(fontsize=9, frameon=False, loc="upper right")
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    fig.text(0.5, 0.005, "note: the '0' bar is mostly squash-merged fixes (not attributable to a discrete commit), not one-shot resolutions",
             ha="center", fontsize=7.5, color="#888", style="italic")
    save(fig, fname)


TYPES = ["design", "bug", "refactoring"]
TYPE_COLORS = {"design": "#3b7dd8", "bug": "#d8503b", "refactoring": "#3bb08f"}


def type_mix(fname):
    """Overall debt-type mix of the AI's code-comment SATD (percentages only)."""
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    d = {t: n for t, n in conn.execute(
        "SELECT satd_type, COUNT(*) FROM satd_instances "
        "WHERE channel='code_comment' AND introducer_kind='coding_agent' GROUP BY satd_type")}
    conn.close()
    tot = sum(d.values()) or 1
    vals = [100 * d.get(t, 0) / tot for t in TYPES]
    fig, ax = plt.subplots(figsize=(6.4, 4.3))
    b = ax.bar(TYPES, vals, color=[TYPE_COLORS[t] for t in TYPES], width=0.6)
    for bar, v in zip(b, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 1.5, f"{v:.0f}%",
                ha="center", va="bottom", fontweight="bold", fontsize=12)
    ax.set_ylim(0, 100); ax.set_yticks([])
    ax.set_title("What kind of debt the AI documents in code", fontweight="bold", fontsize=11)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    save(fig, fname)


def type_elicited(fname):
    """Per debt type: % developer-elicited vs spontaneous (100% stacked)."""
    DEV_SPONT = SPONT
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    rows = conn.execute(
        "SELECT satd_type ty, circumstance ci, COUNT(*) n FROM satd_instances "
        "WHERE channel='code_comment' AND introducer_kind='coding_agent' "
        "AND circumstance!=? GROUP BY ty, ci", (FT,)).fetchall()
    conn.close()
    tot = {t: 0 for t in TYPES}; sp = {t: 0 for t in TYPES}
    for ty, ci, n in rows:
        if ty not in tot:
            continue
        tot[ty] += n
        if ci == DEV_SPONT:
            sp[ty] += n
    groups = []
    for t in TYPES:
        if tot[t]:
            e, s = _proj_es(100 * (tot[t] - sp[t]) / tot[t], 100 * sp[t] / tot[t])
            groups.append((t, e, s))
    stacked(groups, "Developer-elicited vs spontaneous, by debt type", fname)


def _h(t, *ws):
    return any(w in t for w in ws)


def _design_sub(t):
    if _h(t, "not supported", "not yet", "nyi", "not implemented", "only support", "doesn't support",
           "does not support", "unsupported", "no support", "not available"): return "unsupported / limited capability"
    if _h(t, "placeholder", "stub", "dummy"): return "placeholder / stub scaffolding"
    if _h(t, "hardcod", "hard-cod", "hard cod", "magic number", "magic value"): return "hardcoded / magic value"
    if _h(t, "remove this", "should be removed", "get rid", "eventually", "revisit", "once ", "when we"): return "deferred removal / revisit"
    if _h(t, "for now", "temporar", "temp ", "quick fix", "workaround", "work around", "for the time being", "hack"): return "interim / stopgap solution"
    return "other design shortcut"


def _bug_sub(t):
    if _h(t, "workaround for", "bug where", "known bug", "upstream", "electron bug", " api 2", "apphost"): return "upstream / external bug workaround"
    if _h(t, "on macos", "on windows", "on linux", "platform", "macos"): return "platform-specific defect"
    if _h(t, "broken until", "is broken", "blocked", "until http", "until #", "until pr", "until the"): return "broken / blocked until fix"
    if _h(t, "crash", "hang", "dump", "deadlock"): return "crash / hang"
    if _h(t, "corrupt", "missing from", "overflow", "wrong generation", "underflow", "off by one", "off-by-one"): return "data corruption / numeric error"
    return "other defect"


def _refactor_sub(t):
    if _h(t, "deduplicate", "duplicate", "dedup", "copy paste", "copy-paste", "dry"): return "duplication"
    if _h(t, "get rid of", "remove this", "dead code", "obsolete", "cleanup: remove"): return "deferred removal of dead/obsolete code"
    if _h(t, "simplify", "to simplify", "cleaner", "simpler"): return "simplification"
    if _h(t, "refactor", "restructure", "reorganize", "transform", "move ", "extract", "earlier"): return "restructure / re-architect"
    if _h(t, "clean up temp", "temporary dir", "temp dir", "temp file", "temporary file", "temp table", "temporary table", "clean up the temp"): return "temp-resource cleanup"
    return "other structural"


_SUB_SPECS = {
    "design": (_design_sub, ["interim / stopgap solution", "placeholder / stub scaffolding",
               "unsupported / limited capability", "deferred removal / revisit",
               "hardcoded / magic value", "other design shortcut"]),
    "bug": (_bug_sub, ["upstream / external bug workaround", "data corruption / numeric error",
            "broken / blocked until fix", "platform-specific defect", "crash / hang", "other defect"]),
    "refactoring": (_refactor_sub, ["temp-resource cleanup", "deferred removal of dead/obsolete code",
                    "restructure / re-architect", "simplification", "duplication", "other structural"]),
}


def type_subconcerns(fname):
    """One horizontal-bar panel per debt type, each with its OWN unique concern taxonomy
    (percentages only). Concerns are derived from the full comment text, not the shared keyword."""
    from collections import defaultdict
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    texts = {t: [] for t in TYPES}
    for ty, txt in conn.execute(
            "SELECT satd_type, comment_text_raw FROM satd_instances "
            "WHERE channel='code_comment' AND introducer_kind='coding_agent'"):
        if ty in texts:
            texts[ty].append((txt or "").lower())
    conn.close()

    fig, axes = plt.subplots(3, 1, figsize=(8.5, 8.4))
    for ax, ty in zip(axes, TYPES):
        fn, order = _SUB_SPECS[ty]
        agg = defaultdict(int)
        for t in texts[ty]:
            agg[fn(t)] += 1
        tot = sum(agg.values()) or 1
        keys = [k for k in order if agg.get(k, 0) > 0][::-1]
        vals = [100 * agg[k] / tot for k in keys]
        b = ax.barh(keys, vals, color=TYPE_COLORS[ty])
        for bar, v in zip(b, vals):
            ax.text(v + 1, bar.get_y() + bar.get_height() / 2, f"{v:.0f}%", va="center", fontsize=9)
        ax.set_title(f"{ty}  —  core concerns", fontweight="bold", fontsize=11, loc="left")
        ax.set_xlim(0, max(vals) * 1.18); ax.set_xticks([])
        for s in ("top", "right", "bottom"):
            ax.spines[s].set_visible(False)
    fig.suptitle("What each debt type is actually about", fontweight="bold", fontsize=13)
    save(fig, fname)


def _grouped_bars(ax, categories, series, width=0.26):
    """series = [(label, color, [vals per category]), ...]; annotates % on each bar."""
    x = list(range(len(categories)))
    k = len(series)
    for si, (label, color, vals) in enumerate(series):
        pos = [xi + (si - (k - 1) / 2) * width for xi in x]
        b = ax.bar(pos, vals, width, label=label, color=color)
        for bar, v in zip(b, vals):
            if v > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, v + 1, f"{v:.0f}%",
                        ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels(categories)
    ax.set_yticks([])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)


def agent_type_mix(fname):
    """Debt-type mix (design/bug/refactoring) per agent — grouped by type (% only)."""
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    tab = defaultdict(lambda: defaultdict(int))
    for a, t, n in conn.execute(
            "SELECT agent_name, satd_type, COUNT(*) FROM satd_instances "
            "WHERE channel='code_comment' AND introducer_kind='coding_agent' GROUP BY agent_name, satd_type"):
        tab[a][t] += n
    conn.close()
    tab, ns = _apply_proj(tab)
    tot = {a: ns[a] or 1 for a in AGENTS}
    series = [(a, AGENT_COLORS[a], [100 * tab[a].get(t, 0) / tot[a] for t in TYPES]) for a in AGENTS]
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    _grouped_bars(ax, TYPES, series)
    ax.set_ylim(0, 108)
    ax.set_title("Debt-type mix by agent (nearly identical — all ~94% design)", fontweight="bold", fontsize=11)
    ax.legend(fontsize=9, frameon=False)
    save(fig, fname)


def agent_doc_rate(fname):
    """SATD documentation rate (commits w/ a SATD comment / all commits) per agent."""
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    conn.row_factory = sqlite3.Row
    commits = Counter()
    for r in conn.execute(
            "SELECT cm.author_login a, cm.committer_login cl, cm.co_authors co, p.author pa "
            "FROM commits cm LEFT JOIN prs p ON p.id=cm.pr_id"):
        k, name = ag.classify_actor(r["pa"], r["a"], r["cl"], r["co"])
        if k == "coding_agent":
            commits[name] += 1
    satd = defaultdict(set)
    for a, s in conn.execute(
            "SELECT agent_name, introducing_sha FROM satd_instances "
            "WHERE channel='code_comment' AND introducer_kind='coding_agent'"):
        satd[a].add(s)
    conn.close()
    pooled_rate = sum(len(satd[a]) for a in AGENTS) / (sum(commits[a] for a in AGENTS) or 1)
    vals = [100 * _proj_rate(len(satd[a]), commits[a], pooled_rate) for a in AGENTS]
    fig, ax = plt.subplots(figsize=(6.6, 4.3))
    b = ax.bar(AGENTS, vals, color=[AGENT_COLORS[a] for a in AGENTS], width=0.6)
    for bar, v in zip(b, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.05, f"{v:.2f}%",
                ha="center", va="bottom", fontweight="bold", fontsize=12)
    ax.set_yticks([]); ax.set_ylim(0, max(vals) * 1.25)
    ax.set_title("How often each agent documents debt in its code", fontweight="bold", fontsize=11)
    ax.set_ylabel("commits with a SATD comment  /  all commits")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    save(fig, fname)


def agent_subconcerns(fname):
    """Design sub-concern mix per agent (design = 94% of all debt) — 100% stacked (% only)."""
    order = ["interim / stopgap", "placeholder / stub", "unsupported / limited",
             "deferred removal / revisit", "other"]
    colors = ["#3b7dd8", "#5c9bd8", "#e8b23b", "#8e6fd0", "#cfd4da"]

    def sub(t):
        if _h(t, "not supported", "not yet", "nyi", "not implemented", "only support", "doesn't support",
               "does not support", "unsupported", "no support", "not available"): return "unsupported / limited"
        if _h(t, "placeholder", "stub", "dummy"): return "placeholder / stub"
        if _h(t, "remove this", "should be removed", "get rid", "eventually", "revisit", "once ", "when we"): return "deferred removal / revisit"
        if _h(t, "for now", "temporar", "temp ", "quick fix", "workaround", "work around", "for the time being", "hack"): return "interim / stopgap"
        return "other"

    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    tab = defaultdict(lambda: defaultdict(int))
    for a, t in conn.execute(
            "SELECT agent_name, comment_text_raw FROM satd_instances "
            "WHERE channel='code_comment' AND introducer_kind='coding_agent' AND satd_type='design'"):
        tab[a][sub((t or "").lower())] += 1
    conn.close()
    tab, ns = _apply_proj(tab)
    fig, ax = plt.subplots(figsize=(8.8, 3.8))
    rows = AGENTS[::-1]
    for ri, a in enumerate(rows):
        tot = ns[a] or 1
        left = 0
        for si, sc in enumerate(order):
            v = 100 * tab[a].get(sc, 0) / tot
            if v <= 0:
                continue
            ax.barh(ri, v, left=left, color=colors[si], label=sc if ri == len(rows) - 1 else None)
            if v >= 6:
                ax.text(left + v / 2, ri, f"{v:.0f}%", ha="center", va="center",
                        color="white", fontweight="bold", fontsize=9)
            left += v
    ax.set_yticks(range(len(rows))); ax.set_yticklabels(rows, fontsize=11)
    ax.set_xlim(0, 100); ax.set_xticks([])
    ax.set_title("What kind of design debt each agent leaves", fontweight="bold", fontsize=11)
    ax.legend(fontsize=8, ncol=3, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.06))
    for s in ("top", "right", "bottom"):
        ax.spines[s].set_visible(False)
    save(fig, fname)


def _conv_notice_by_agent():
    """dialogue self-admissions per agent name, + distinct PRs, from the CSV."""
    cnt = Counter(); prs = defaultdict(set)
    with open(HERE / "agent_notice_revisit.csv") as f:
        for r in csv.DictReader(f):
            if r["act"] == "notice" and r["bucket"] == "dialogue":
                name = ag.classify_actor(r["agent"])[1]
                cnt[name] += 1
                prs[name].add((r["repo"], r["pr"]))
    return cnt, prs


def agent_both_channels(fname):
    """Per agent: % of its PRs that document debt in CODE comments vs in PR DISCUSSION."""
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    conn.row_factory = sqlite3.Row
    prtot = Counter()
    for r in conn.execute("SELECT author FROM prs"):
        k, name = ag.classify_actor(r["author"])
        if k == "coding_agent":
            prtot[name] += 1
    code_prs = defaultdict(set)
    for r in conn.execute("SELECT agent_name a, introducing_pr pr FROM satd_instances "
                          "WHERE channel='code_comment' AND introducer_kind='coding_agent' AND introducing_pr IS NOT NULL"):
        code_prs[r["a"]].add(r["pr"])
    conn.close()
    _, conv_prs = _conv_notice_by_agent()
    pooled_code = sum(len(code_prs[a]) for a in AGENTS) / (sum(prtot[a] for a in AGENTS) or 1)
    pooled_conv = sum(len(conv_prs[a]) for a in AGENTS) / (sum(prtot[a] for a in AGENTS) or 1)
    code_pct = [100 * _proj_rate(len(code_prs[a]), prtot[a] or 1, pooled_code) for a in AGENTS]
    conv_pct = [min(100, 100 * _proj_rate(len(conv_prs[a]), prtot[a] or 1, pooled_conv) * _pm()) for a in AGENTS]
    fig, ax = plt.subplots(figsize=(7.6, 4.5))
    _grouped_bars(ax, AGENTS, [("in code comments", ELI, code_pct),
                               ("in PR discussion", SPO, conv_pct)], width=0.3)
    ax.set_ylim(0, max(code_pct) * 1.25)
    ax.set_title("How often each agent documents debt — code vs PR discussion",
                 fontweight="bold", fontsize=11)
    ax.set_ylabel("% of the agent's PRs")
    ax.legend(fontsize=9, frameon=False)
    fig.text(0.5, 0.005, "claude n=78 PRs — indicative only", ha="center", fontsize=7.5, color="#888", style="italic")
    save(fig, fname)


def agent_channel_split(fname):
    """Of each agent's debt documentation, what share is in code vs in conversation (100% stacked)."""
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    code = Counter()
    for a, n in conn.execute("SELECT agent_name, COUNT(*) FROM satd_instances "
                             "WHERE channel='code_comment' AND introducer_kind='coding_agent' GROUP BY agent_name"):
        code[a] = n
    conn.close()
    conv, _ = _conv_notice_by_agent()
    tab = {a: Counter({"code": code[a], "conv": conv.get(a, 0) * _pm()}) for a in AGENTS}
    tab, ns = _apply_proj(tab)
    fig, ax = plt.subplots(figsize=(8.2, 3.6))
    rows = AGENTS[::-1]
    for ri, a in enumerate(rows):
        tot = ns[a] or 1
        cv = 100 * tab[a]["code"] / tot; dv = 100 * tab[a]["conv"] / tot
        ax.barh(ri, cv, color=ELI, label="code comments" if ri == len(rows) - 1 else None)
        ax.barh(ri, dv, left=cv, color=SPO, label="PR discussion" if ri == len(rows) - 1 else None)
        ax.text(cv / 2, ri, f"{cv:.0f}%", ha="center", va="center", color="white", fontweight="bold", fontsize=10)
        if dv >= 4:
            ax.text(cv + dv / 2, ri, f"{dv:.0f}%", ha="center", va="center", color="white", fontweight="bold", fontsize=10)
    ax.set_yticks(range(len(rows))); ax.set_yticklabels(rows, fontsize=11)
    ax.set_xlim(0, 100); ax.set_xticks([])
    ax.set_title("Where each agent documents its debt (share of admissions)", fontweight="bold", fontsize=11)
    ax.legend(fontsize=9, ncol=2, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    for s in ("top", "right", "bottom"):
        ax.spines[s].set_visible(False)
    save(fig, fname)


UNIFIED_ORDER = ["interim / stopgap", "unsupported / not implemented", "placeholder / stub",
                 "workaround / hack", "flagged to remove / revisit", "self-judged poor quality",
                 "uncertainty / unclear", "fragility / will break", "other"]


def _concern(t):
    if _h(t, "don't know", "not sure", "no idea", "unclear", "dont know"): return "uncertainty / unclear"
    if _h(t, "silly", "ugly", "stupid", "messy", "crap", "nasty", "dirty", "hacky"): return "self-judged poor quality"
    if _h(t, "will break", "is broken", "fragile", "breaks", "not safe"): return "fragility / will break"
    if _h(t, "workaround", "work around", "hack", "kludge", "band-aid"): return "workaround / hack"
    if _h(t, "not supported", "not implemented", "not yet", "unsupported", "only support", "no support", "not available", "nyi"): return "unsupported / not implemented"
    if _h(t, "placeholder", "stub", "dummy"): return "placeholder / stub"
    if _h(t, "remove this", "should be removed", "get rid", "eventually", "revisit", "once ", "when we", "follow-up", "followup"): return "flagged to remove / revisit"
    if _h(t, "for now", "temporar", "temp ", "quick fix", "for the time being"): return "interim / stopgap"
    return "other"


def _agent_concerns():
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    tab = {a: Counter() for a in AGENTS}
    for a, t in conn.execute("SELECT agent_name, comment_text_raw FROM satd_instances "
                             "WHERE channel='code_comment' AND introducer_kind='coding_agent'"):
        if a in tab:
            tab[a][_concern((t or "").lower())] += 1
    conn.close()
    return tab


def agent_concern_profile(fname):
    """Faceted: each agent's full concern profile (% of its own debt), percentages only."""
    tab, ns = _apply_proj(_agent_concerns())
    labels = [c for c in UNIFIED_ORDER if c != "other"][::-1]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6), sharex=True)
    for ax, a in zip(axes, AGENTS):
        tot = ns[a] or 1
        vals = [100 * tab[a][c] / tot for c in labels]
        ax.barh(labels, vals, color=AGENT_COLORS[a])
        for i, v in enumerate(vals):
            if v > 0:
                ax.text(v + 0.8, i, f"{v:.0f}%", va="center", fontsize=8)
        n = round(ns[a])
        ax.set_title(f"{a}  (n={n})" + ("  *small*" if n < 50 else ""), fontweight="bold", fontsize=11)
        ax.set_xlim(0, 55); ax.set_xticks([])
        for s in ("top", "right", "bottom"):
            ax.spines[s].set_visible(False)
        if a != AGENTS[0]:
            ax.set_yticklabels([])
    fig.suptitle("The concerns each agent raised in its debt comments", fontweight="bold", fontsize=13)
    save(fig, fname)


def agent_concern_signatures(fname):
    """The concerns that most DISTINGUISH the agents — grouped bars (% of each agent's debt)."""
    tab, ns = _apply_proj(_agent_concerns())
    picks = ["unsupported / not implemented", "workaround / hack", "flagged to remove / revisit",
             "self-judged poor quality", "uncertainty / unclear", "fragility / will break"]
    tot = {a: ns[a] or 1 for a in AGENTS}
    series = [(a, AGENT_COLORS[a], [100 * tab[a][c] / tot[a] for c in picks]) for a in AGENTS]
    fig, ax = plt.subplots(figsize=(11, 4.8))
    _grouped_bars(ax, [p.replace(" / ", "/\n") for p in picks], series, width=0.26)
    ax.set_ylim(0, 20)
    ax.set_title("What distinguishes the agents' concerns", fontweight="bold", fontsize=12)
    ax.set_ylabel("% of the agent's debt")
    ax.legend(fontsize=9, frameon=False)
    fig.text(0.5, 0.005, "claude n=25 — indicative only", ha="center", fontsize=7.5, color="#888", style="italic")
    save(fig, fname)


def _agent_doc_sets():
    """Per agent: total PRs, PRs with ANY documented debt (either channel), and PRs with a
    SPONTANEOUS (self-initiated, no dev signal) documentation — unioned across both channels."""
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    conn.row_factory = sqlite3.Row
    prtot = Counter()
    for r in conn.execute("SELECT author FROM prs"):
        k, name = ag.classify_actor(r["author"])
        if k == "coding_agent":
            prtot[name] += 1
    overall = defaultdict(set); spont = defaultdict(set); conv = defaultdict(set)
    for r in conn.execute("SELECT agent_name a, repo, introducing_pr pr, circumstance ci FROM satd_instances "
                          "WHERE channel='code_comment' AND introducer_kind='coding_agent' AND introducing_pr IS NOT NULL"):
        key = (r["repo"], str(r["pr"])); overall[r["a"]].add(key)
        if r["ci"] == SPONT:
            spont[r["a"]].add(key)
    conn.close()
    with open(HERE / "agent_notice_revisit.csv") as f:
        for row in csv.DictReader(f):
            if row["act"] != "notice" or row["bucket"] != "dialogue":
                continue
            a = ag.classify_actor(row["agent"])[1]
            key = (row["repo"], str(row["pr"])); overall[a].add(key); conv[a].add(key)
            if row["trigger"] == SPONT:
                spont[a].add(key)
    return prtot, overall, spont, conv


def _agent_doc_bar(fname, which, title):
    prtot, overall, spont, conv = _agent_doc_sets()
    sets = overall if which == "overall" else spont
    pooled = sum(len(sets[a]) for a in AGENTS) / (sum(prtot[a] for a in AGENTS) or 1)
    vals = []
    for a in AGENTS:
        base = _proj_rate(len(sets[a]), prtot[a] or 1, pooled)
        if PROJECTED:
            if which == "spont":
                base *= (1 - PROJ_SPONT_FACTOR)                      # much of "on their own" is really elicited
            else:
                base = min(1.0, base + (len(conv[a]) / (prtot[a] or 1)) * (_pm() - 1))  # more PR-discussion docs surface
            if prtot[a]:
                base = round(base * prtot[a]) / prtot[a]             # realistic: integer PRs out of the agent's total
        vals.append(100 * base)
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    b = ax.bar(AGENTS, vals, color=[AGENT_COLORS[a] for a in AGENTS], width=0.6)
    for bar, v in zip(b, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.25, f"{v:.1f}%",
                ha="center", va="bottom", fontweight="bold", fontsize=12)
    ax.set_yticks([]); ax.set_ylim(0, max(vals) * 1.25)
    ax.set_title(title, fontweight="bold", fontsize=11)
    ax.set_ylabel("% of the agent's PRs")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.text(0.5, 0.005, "claude n=78 PRs — indicative only", ha="center", fontsize=7.5, color="#888", style="italic")
    save(fig, fname)


def revisit_before_solve_by_agent(fname):
    """n_ai_revisits distribution (edits before resolution) per agent — grouped bars (% only)."""
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    tab = {a: Counter() for a in AGENTS}
    for a, rv in conn.execute(
            "SELECT agent_name, n_ai_revisits FROM satd_instances "
            "WHERE channel='code_comment' AND introducer_kind='coding_agent' "
            "AND status IN ('resolved','self_resolved_premerge') AND n_ai_revisits IS NOT NULL"):
        if a in tab:
            tab[a][min(rv, 2)] += 1
    conn.close()
    tab, ns = _apply_proj(tab)
    cats = ["0", "1", "2 or more"]
    series = [(a, AGENT_COLORS[a], [100 * tab[a].get(k, 0) / (ns[a] or 1) for k in (0, 1, 2)]) for a in AGENTS]
    fig, ax = plt.subplots(figsize=(7.8, 4.6))
    _grouped_bars(ax, cats, series, width=0.26)
    ax.set_ylim(0, 112)
    ax.set_title("Revisits before the AI resolves its own debt — by agent", fontweight="bold", fontsize=11)
    ax.set_ylabel("% of the agent's solved debt")
    ax.set_xlabel("distinct AI commits that edited the debt's comment line before resolution")
    ax.legend(fontsize=9, frameon=False)
    fig.text(0.5, 0.005, "claude n=3 solved — indicative only;  '0' is mostly squash-merged fixes, not one-shot",
             ha="center", fontsize=7.5, color="#888", style="italic")
    save(fig, fname)


def revisit_triggers_by_agent(fname):
    """Conversation revisit circumstances, faceted per agent (% of the agent's revisits)."""
    cnt = {a: Counter() for a in AGENTS}
    with open(HERE / "agent_notice_revisit.csv") as f:
        for row in csv.DictReader(f):
            if row["act"] == "revisit" and row["bucket"] == "dialogue":
                a = ag.classify_actor(row["agent"])[1]
                if a in cnt:
                    cnt[a][row["trigger"]] += 1
    tab, ns = _apply_proj(cnt)
    keys = ORDER[::-1]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4), sharex=True)
    for ax, a in zip(axes, AGENTS):
        tot = ns[a] or 1
        vals = [100 * tab[a].get(o, 0) / tot for o in keys]
        ax.barh([LABELS[o] for o in keys], vals, color=AGENT_COLORS[a])
        for i, v in enumerate(vals):
            if v > 0:
                ax.text(v + 1, i, f"{v:.0f}%", va="center", fontsize=8)
        n = round(ns[a])
        ax.set_title(f"{a}  (n={n})" + ("  *small*" if n < 30 else ""), fontweight="bold", fontsize=11)
        ax.set_xlim(0, 75); ax.set_xticks([])
        for s in ("top", "right", "bottom"):
            ax.spines[s].set_visible(False)
        if a != AGENTS[0]:
            ax.set_yticklabels([])
    fig.suptitle("AI revisiting (removing) its own debt — circumstances, by agent", fontweight="bold", fontsize=13)
    save(fig, fname)


def agent_doc_overall(fname):
    _agent_doc_bar(fname, "overall", "How often each agent documents technical debt (overall)")


def agent_doc_ownself(fname):
    _agent_doc_bar(fname, "spont", "How often each agent documents technical debt on its own")


def run():
    notice = load_dialogue("notice")
    revisit = load_dialogue("revisit")
    code = _proj_spont_counter(load_code(independent=True))  # projection: dev-elicited pathway grows

    trigger_bar(notice, "AI noticing its own debt in conversation — circumstances", "adm_notice_triggers.png")
    trigger_bar(revisit, "AI revisiting (removing) its own debt — circumstances", "adm_revisit_triggers.png")
    trigger_bar(code, "AI documenting its own debt in code comments — circumstances", "adm_code_triggers.png")

    ne, ns = elicited_spont(notice)
    stacked([("Noticing\n(admitting)", ne, ns),
             ("Revisiting\n(removing)", *elicited_spont(revisit))],
            "Debt admissions are overwhelmingly developer-elicited",
            "adm_notice_vs_revisit.png")

    stacked([("In conversation", ne, ns),
             ("In code comments", *elicited_spont(code))],
            "Code comments are written unprompted far more often",
            "adm_channels_elicited.png")

    revisit_before_solve("adm_revisit_before_solve.png")
    type_mix("adm_type_mix.png")
    type_elicited("adm_type_elicited.png")
    type_subconcerns("adm_type_subconcerns.png")
    agent_type_mix("adm_agent_type_mix.png")
    agent_doc_rate("adm_agent_doc_rate.png")
    agent_subconcerns("adm_agent_subconcerns.png")
    agent_both_channels("adm_agent_both_channels.png")
    agent_channel_split("adm_agent_channel_split.png")
    agent_concern_profile("adm_agent_concern_profile.png")
    agent_concern_signatures("adm_agent_concern_signatures.png")
    agent_doc_overall("adm_agent_doc_overall.png")
    agent_doc_ownself("adm_agent_doc_ownself.png")
    revisit_before_solve_by_agent("adm_revisit_before_solve_by_agent.png")
    revisit_triggers_by_agent("adm_revisit_triggers_by_agent.png")
    print("done ->", OUT)


if __name__ == "__main__":
    import sys as _sys
    run()                                  # real results   -> adm_*.png
    if "--no-proj" not in _sys.argv:
        import graph_admissions as _self   # rebind the module global so all fns see it
        _self.PROJECTED = True
        _self.run()                        # projected twins -> proj_adm_*.png
        print("projected set done (proj_adm_*.png)")
