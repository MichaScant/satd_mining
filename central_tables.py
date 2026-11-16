"""Two central PREDICTION tables (projected numbers, matching the proj_adm_* graphs), split by
agent. RQ1 and RQ2 are kept SEPARATE.

  RQ1: Do coding agents document the technical debt they introduce, and how often?
  RQ2: How often do agents revisit the technical debt they introduced?

Writes central_tables.md and prints.  python3 central_tables.py
"""
import csv
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import graph_admissions as g
import agents as ag

g.PROJECTED = True                      # <-- produce PREDICTION values (same model as proj_adm_*)
AG = g.AGENTS
BASE = "channel='code_comment' AND introducer_kind='coding_agent'"
DB = HERE / "db" / "satd_mining.db"
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
OUTG = HERE / "graphs"; OUTG.mkdir(exist_ok=True)

out = []
def emit(s=""): print(s); out.append(s)
def pct(x): return f"{x:.0f}%"
W = 50   # label column width
_collect = None   # current RQ's row collector (label, [per-agent strings])


def render_table(title, rows, fname):
    nrow = len(rows) + 1
    fig, ax = plt.subplots(figsize=(11.5, 0.42 * nrow + 0.9))
    ax.axis("off")
    cells = [["metric"] + AG] + [[lbl] + vals for lbl, vals in rows]
    tbl = ax.table(cellText=cells, loc="center", cellLoc="center",
                   colWidths=[0.60, 0.133, 0.133, 0.133])
    tbl.auto_set_font_size(False); tbl.set_fontsize(11); tbl.scale(1, 1.45)
    for (r, col), cell in tbl.get_celld().items():
        cell.set_edgecolor("#d0d7e2")
        if r == 0:
            cell.set_facecolor("#3b7dd8"); cell.set_text_props(color="white", fontweight="bold")
        elif col == 0:
            cell.set_facecolor("#eef2f8"); cell.set_text_props(horizontalalignment="left", fontweight="bold")
        elif r % 2 == 0:
            cell.set_facecolor("#f7f9fc")
        cell.PAD = 0.04
    ax.set_title(title, fontweight="bold", fontsize=13, pad=6, y=0.99)
    fig.text(0.5, 0.012, "⚠ Projection — illustrative values, not real measurements",
             ha="center", va="bottom", color="#c0202a", fontsize=10.5, fontweight="bold", style="italic")
    fig.subplots_adjust(bottom=0.12)
    fig.savefig(OUTG / fname, dpi=160, facecolor="white", bbox_inches="tight"); plt.close(fig)
    print("saved graphs/" + fname)


def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c


# ============================ RQ1: DOCUMENTATION ============================
c = conn()
# code documentation rate (SATD commits / all commits)
commits = Counter(); satd = defaultdict(set)
for r in c.execute("SELECT cm.author_login a, cm.committer_login cl, cm.co_authors co, p.author pa "
                   "FROM commits cm LEFT JOIN prs p ON p.id=cm.pr_id"):
    k, name = ag.classify_actor(r["pa"], r["a"], r["cl"], r["co"])
    if k == "coding_agent":
        commits[name] += 1
for r in c.execute(f"SELECT agent_name a, introducing_sha s FROM satd_instances WHERE {BASE}"):
    satd[r["a"]].add(r["s"])
pooled = sum(len(satd[a]) for a in AG) / (sum(commits[a] for a in AG) or 1)
code_doc = {a: 100 * g._proj_rate(len(satd[a]), commits[a] or 1, pooled) for a in AG}

# overall / on-its-own documentation (% of the agent's PRs), both channels
prtot, overall, spont, convset = g._agent_doc_sets()
pov = sum(len(overall[a]) for a in AG) / (sum(prtot[a] for a in AG) or 1)
psp = sum(len(spont[a]) for a in AG) / (sum(prtot[a] for a in AG) or 1)
ov = {}; own = {}
for a in AG:
    b = g._proj_rate(len(overall[a]), prtot[a] or 1, pov)
    b = min(1.0, b + (len(convset[a]) / (prtot[a] or 1)) * (g._pm() - 1))
    ov[a] = 100 * b
    own[a] = 100 * g._proj_rate(len(spont[a]), prtot[a] or 1, psp) * (1 - g.PROJ_SPONT_FACTOR)

# channel split (code comments vs PR discussion)
codecnt = Counter()
for a, n in c.execute(f"SELECT agent_name, COUNT(*) FROM satd_instances WHERE {BASE} GROUP BY agent_name"):
    codecnt[a] = n
convn, _ = g._conv_notice_by_agent()
tabc = {a: Counter({"code": codecnt[a], "conv": convn.get(a, 0) * g._pm()}) for a in AG}
tabc, nsc = g._apply_proj(tabc)
chan = {a: (100 * tabc[a]["code"] / (nsc[a] or 1), 100 * tabc[a]["conv"] / (nsc[a] or 1)) for a in AG}

# debt-type mix
tt = {a: Counter() for a in AG}
for a, t, n in c.execute(f"SELECT agent_name, satd_type, COUNT(*) FROM satd_instances WHERE {BASE} GROUP BY agent_name, satd_type"):
    if a in tt: tt[a][t] += n
tt, nst = g._apply_proj(tt)
typ = {a: {ty: 100 * tt[a].get(ty, 0) / (nst[a] or 1) for ty in ("design", "bug", "refactoring")} for a in AG}

c.close()

emit("=" * 78)
emit("RQ1 — Do coding agents DOCUMENT the technical debt they introduce, and how often?")
emit("        (PREDICTION / projected values — split by agent)")
emit("=" * 78)
H = f"{'metric':{W}}" + "".join(f"{a:>12}" for a in AG)
emit(H); emit("-" * len(H))
def row(label, d, fmt=pct):
    vals = [fmt(d[a]) for a in AG]
    emit(f"{label:{W}}" + "".join(f"{v:>12}" for v in vals))
    if _collect is not None:
        _collect.append((label.strip(), vals))
rq1_rows = []; _collect = rq1_rows
row("documentation rate — in code (% commits)", code_doc, lambda x: f"{x:.1f}%")
row("documentation rate — overall (% of PRs)", ov)
row("  of which on its OWN (spontaneous)", own)
row("  of which developer-elicited", {a: ov[a] - own[a] for a in AG})
row("where documented: code comments", {a: chan[a][0] for a in AG})
row("where documented: PR discussion", {a: chan[a][1] for a in AG})
row("debt type: design", {a: typ[a]["design"] for a in AG})
row("debt type: bug", {a: typ[a]["bug"] for a in AG})
row("debt type: refactoring", {a: typ[a]["refactoring"] for a in AG})


# ============================ RQ2: REVISITING ============================
c = conn()
tot2 = Counter(); wf = Counter(); wn = Counter(); sumf = Counter()
for r in c.execute(f"SELECT agent_name a, n_ai_file_edits f, n_ai_near_edits n FROM satd_instances "
                   f"WHERE {BASE} AND n_ai_file_edits IS NOT NULL"):
    if r["a"] not in AG: continue
    tot2[r["a"]] += 1; wf[r["a"]] += 1 if r["f"] else 0; wn[r["a"]] += 1 if r["n"] else 0; sumf[r["a"]] += r["f"]
pf = sum(wf.values()) / (sum(tot2.values()) or 1); pn = sum(wn.values()) / (sum(tot2.values()) or 1)
file_rate = {a: 100 * g._proj_rate(wf[a], tot2[a] or 1, pf) for a in AG}
near_rate = {a: 100 * g._proj_rate(wn[a], tot2[a] or 1, pn) for a in AG}
mean_file = {a: (sumf[a] / tot2[a]) if tot2[a] else 0 for a in AG}

# revisits before the fix (on solved debt): 0 / 1 / 2+
solv = {a: Counter() for a in AG}
for r in c.execute(f"SELECT agent_name a, n_ai_revisits rv FROM satd_instances WHERE {BASE} "
                   f"AND status IN ('resolved','self_resolved_premerge') AND n_ai_revisits IS NOT NULL"):
    if r["a"] in solv: solv[r["a"]][min(r["rv"], 2)] += 1
solv, nss = g._apply_proj(solv)
sd0 = {a: 100 * solv[a].get(0, 0) / (nss[a] or 1) for a in AG}
sd1 = {a: 100 * solv[a].get(1, 0) / (nss[a] or 1) for a in AG}
sd2 = {a: 100 * solv[a].get(2, 0) / (nss[a] or 1) for a in AG}

# conversation revisit: elicited vs spontaneous
rv = {a: Counter() for a in AG}
with open(HERE / "agent_notice_revisit.csv") as f:
    for row_ in csv.DictReader(f):
        if row_["act"] == "revisit" and row_["bucket"] == "dialogue":
            a = ag.classify_actor(row_["agent"])[1]
            if a in rv: rv[a][row_["trigger"]] += 1
rv, nsr = g._apply_proj(rv)
rev = {}
for a in AG:
    tot = nsr[a] or 1; sp = rv[a].get(g.SPONT, 0)
    rev[a] = g._proj_es(100 * (tot - sp) / tot, 100 * sp / tot)
c.close()

emit("\n\n" + "=" * 78)
emit("RQ2 — How often do agents REVISIT the technical debt they introduced?")
emit("        (PREDICTION / projected values — split by agent)")
emit("=" * 78)
emit(H); emit("-" * len(H))
rq2_rows = []; _collect = rq2_rows
row("revisits the file before resolution (% of debt)", file_rate)
row("  of which edits NEAR the SATD (% of debt)", near_rate)
row("mean AI file-edits per debt", mean_file, lambda x: f"{x:.2f}")
row("of SOLVED debt — 0 edits before fix", sd0)
row("                 1 edit", sd1)
row("                 2+ edits", sd2)
row("convo revisit — developer-elicited", {a: rev[a][0] for a in AG})
row("convo revisit — spontaneous", {a: rev[a][1] for a in AG})

emit("\n(PREDICTION values use the projection model: small-sample agents — esp. claude — are "
     "regularised toward the pooled distribution; see proj_adm_* graphs. Not real measurements.)")
render_table("RQ1 — Do agents document the debt they introduce?", rq1_rows, "table_rq1.png")
render_table("RQ2 — How often do agents revisit their debt?", rq2_rows, "table_rq2.png")
(HERE / "central_tables.md").write_text("\n".join(out))
emit(f"\n-> saved central_tables.md + graphs/table_rq1.png + graphs/table_rq2.png")
