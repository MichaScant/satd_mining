"""Graphs: AI noticing vs revisiting its own technical debt, with circumstances.

Data-driven (no hardcoded counts): dialogue triggers are read from
agent_notice_revisit.csv (produced by analyze_reviewer_elicited_satd.py); code-comment
triggers are read from the satd_instances.circumstance column that the same script fills.
Annotations show PERCENTAGES ONLY — no counts, no n=.
"""
import csv
import sqlite3
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
OUT = HERE / "graphs"; OUT.mkdir(exist_ok=True)
ELI, SPO = "#3b7dd8", "#e07b39"

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


def save(fig, name):
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
    return 100 * (tot - sp) / tot, 100 * sp / tot


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
            e = 100 * (tot[t] - sp[t]) / tot[t]; s = 100 * sp[t] / tot[t]
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


def run():
    notice = load_dialogue("notice")
    revisit = load_dialogue("revisit")
    code = load_code(independent=True)

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
    print("done ->", OUT)


if __name__ == "__main__":
    run()
