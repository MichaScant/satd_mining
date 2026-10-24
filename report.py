"""Report — headline numbers for the study's three questions, straight from the DB.

Answers, per the project brief:
  Q1  Who RESOLVED the debt — AI or human? If human, how much AI was involved
      (commits by AI in the resolving PR + AI<->dev discussion turns)?
  Q2  Who POINTED IT OUT first — AI or human, at commit level (self-admission in code)
      or file level (reviewer inline flag)?
  Q3  Was the resolution SUCCESSFUL (real code change) vs a silent comment deletion?
  + survival time (merge -> resolved / censored) and the two SATD channels.

Prints a text report and writes a few PNGs to ./graphs/.

Run:  python report.py [--db db/satd_mining.db] [--out graphs]
"""
import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import config

AI_C, HUMAN_C, OTHER_C = "#e07b39", "#3b7dd8", "#9aa0a6"  # AI=warm, human=blue, other=grey
TYPE_C = {"design": "#6c5ce7", "bug": "#d63031", "refactoring": "#00a884"}


class _Tee:
    """Write to several streams at once (console + report.txt)."""
    def __init__(self, *streams):
        self.streams = streams
    def write(self, s):
        for st in self.streams:
            st.write(s)
    def flush(self):
        for st in self.streams:
            st.flush()


def pct(n, d):
    return f"{100*n/d:.1f}%" if d else "n/a"


def table(rows, headers):
    """Simple fixed-width text table."""
    cols = list(zip(*([headers] + rows))) if rows else [[h] for h in headers]
    widths = [max(len(str(x)) for x in c) for c in cols]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths))
    out = [line(headers), "  ".join("-" * w for w in widths)]
    out += [line(r) for r in rows]
    return "\n".join("  " + o for o in out)


def main(db, out):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    q = lambda s, *a: conn.execute(s, a).fetchall()
    one = lambda s, *a: conn.execute(s, a).fetchone()[0]

    total = one("SELECT COUNT(*) FROM satd_instances")
    print("=" * 66)
    print(f"  AI TECHNICAL-DEBT MINING — REPORT   ({db})")
    print("=" * 66)
    if not total:
        print("\n  No SATD instances in the DB yet — run stages 3 / 3b first.\n")
        return

    # ---------------------------------------------------------------- 1. corpus
    print(f"\n[1] CORPUS — {total} SATD instances")
    by_channel = Counter({r["channel"] or "unknown": r["n"] for r in
                          q("SELECT channel, COUNT(*) n FROM satd_instances GROUP BY channel")})
    by_type = Counter({r["satd_type"] or "unknown": r["n"] for r in
                       q("SELECT satd_type, COUNT(*) n FROM satd_instances GROUP BY satd_type")})
    by_intro = Counter({r["author_class"] or "unknown": r["n"] for r in
                        q("SELECT author_class, COUNT(*) n FROM satd_instances GROUP BY author_class")})
    print("  channel :", ", ".join(f"{k}={v} ({pct(v,total)})" for k, v in by_channel.most_common()))
    print("  type    :", ", ".join(f"{k}={v} ({pct(v,total)})" for k, v in by_type.most_common()))
    print("  introduced by:", ", ".join(f"{k}={v} ({pct(v,total)})" for k, v in by_intro.most_common()))

    # ---------------------------------------------------------------- 2. detection rates
    print("\n[2] DETECTION RATES")
    n_commits = one("SELECT COUNT(DISTINCT sha) FROM commits") or 0
    cc_commits = one("SELECT COUNT(DISTINCT introducing_sha) FROM satd_instances WHERE channel='code_comment'")
    print(f"  code-comment SATD: {cc_commits}/{n_commits} commits = {pct(cc_commits, n_commits)}"
          f"   (baseline AI 2.6% / human 4.25%)")
    for at in ("ai", "human"):   # per-actor code-comment rate, directly vs Brahmi's split
        com = one("SELECT COUNT(DISTINCT sha) FROM commits WHERE author_class=?", at)
        ccx = one("SELECT COUNT(DISTINCT introducing_sha) FROM satd_instances "
                  "WHERE channel='code_comment' AND author_class=?", at)
        base = "2.6%" if at == "ai" else "4.25%"
        print(f"  code-comment ({at:>5}): {ccx}/{com} commits = {pct(ccx, com)}   (baseline {base})")
    for at in ("ai", "human"):
        utt = one("SELECT COUNT(*) FROM review_events WHERE actor_type=? AND body IS NOT NULL", at)
        hit = one("SELECT COUNT(*) FROM satd_instances WHERE channel='conversation' AND author_class=? "
                  "AND source_kind IN ('agent_reply','developer_request')", at)
        print(f"  conversational ({at:>5}): {hit}/{utt} utterances = {pct(hit, utt)}  (NEW — no baseline)")

    # ---------------------------------------------------------------- 3. first pointer (Q2)
    print("\n[3] WHO POINTED IT OUT FIRST  (Q2)")
    # AI-vs-human only, over the coding-agent + human cohort (automation excluded)
    fp = q("SELECT first_pointer_class cls, COALESCE(first_pointer_level,'unset') lvl, COUNT(*) n "
           "FROM satd_instances WHERE first_pointer_class IN ('ai','human') "
           "AND introducer_kind IN ('coding_agent','human') GROUP BY cls, lvl")
    fp_total = sum(r["n"] for r in fp)
    if fp_total:
        rows = [[r["cls"], r["lvl"], r["n"], pct(r["n"], fp_total)] for r in fp]
        print(table(rows, ["first-pointer", "level", "n", "share"]))
        print("  (commit level = SATD self-admission in code; file level = reviewer inline flag)")
    else:
        print("  no first-pointer data yet (run stage3c).")

    # ---------------------------------------------------------------- 4. who resolved (Q1)
    print("\n[4] WHO RESOLVED IT  (Q1)")
    resolved_states = ("resolved", "self_resolved_premerge")
    ph = ",".join("?" * len(resolved_states))
    res = q(f"SELECT COALESCE(resolver_class,'unknown') cls, COUNT(*) n FROM satd_instances "
            f"WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') GROUP BY cls", *resolved_states)
    n_res = sum(r["n"] for r in res)
    if n_res:
        print("  among", n_res, "resolved:",
              ", ".join(f"{r['cls']}={r['n']} ({pct(r['n'], n_res)})" for r in res))
        # AI involvement in HUMAN-resolved instances
        hr = one(f"SELECT COUNT(*) FROM satd_instances WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') AND resolver_class='human'",
                 *resolved_states)
        if hr:
            with_ai = one(f"SELECT COUNT(*) FROM satd_instances WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') AND resolver_class='human' "
                          f"AND (COALESCE(resolver_ai_commits,0)>0 OR COALESCE(resolver_ai_discussion,0)>0)",
                          *resolved_states)
            avg_c = one(f"SELECT AVG(COALESCE(resolver_ai_commits,0)) FROM satd_instances "
                        f"WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') AND resolver_class='human'", *resolved_states) or 0
            avg_d = one(f"SELECT AVG(COALESCE(resolver_ai_discussion,0)) FROM satd_instances "
                        f"WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') AND resolver_class='human'", *resolved_states) or 0
            print(f"  of {hr} human-resolved: {with_ai} ({pct(with_ai, hr)}) had AI involvement in the fix;"
                  f"  avg AI commits={avg_c:.2f}, avg AI discussion turns={avg_d:.2f}")
    else:
        print("  no resolved instances yet (run stage5).")

    # ---------------------------------------------------------------- 5. success (Q3)
    print("\n[5] WAS THE RESOLUTION SUCCESSFUL  (Q3)")
    st = Counter({r["status"] or "untracked": r["n"] for r in
                  q("SELECT status, COUNT(*) n FROM satd_instances "
                    "WHERE introducer_kind IN ('coding_agent','human') GROUP BY status")})
    st_total = sum(st.values()) or 1
    tracked = sum(v for k, v in st.items() if k != "untracked")
    for k, v in st.most_common():
        print(f"  {k:24} {v:6}  ({pct(v, st_total)})")
    genuine = st.get("resolved", 0) + st.get("self_resolved_premerge", 0)
    silent = st.get("comment_deleted_only", 0)
    print(f"  -> genuine code-change resolutions: {genuine}"
          f"   | silent comment-only deletions: {silent}"
          f"   | success rate among resolved: {pct(genuine, genuine + silent)}")

    # ---------------------------------------------------------------- 6. survival
    print("\n[6] SURVIVAL (AI-introduced code-comment SATD; days merge -> resolved / censored)")
    durs = q("SELECT survival_days d, status FROM satd_instances "
             "WHERE survival_days IS NOT NULL AND introducer_kind='coding_agent'")
    if durs:
        ev = [(r["d"], 1 if r["status"] in resolved_states else 0) for r in durs]
        res_days = sorted(d for d, e in ev if e)
        med = res_days[len(res_days) // 2] if res_days else None
        censored = sum(1 for _, e in ev if not e)
        print(f"  n with survival_days={len(ev)}  (events={len(res_days)}, censored/alive={censored})")
        if med is not None:
            print(f"  median time-to-resolution (resolved only): {med:.1f} days")
        km_x, km_y, km_med = kaplan_meier(ev)
    else:
        print("  no survival data yet (run stage5).")
        km_x = km_y = km_med = None

    # ---------------------------------------------------------------- 7. agents document & revisit
    print("\n[7] CODING AGENTS: DOCUMENTING & REVISITING THEIR OWN DEBT")
    agent_satd_commits = one("SELECT COUNT(DISTINCT introducing_sha) FROM satd_instances "
                             "WHERE introducer_kind='coding_agent'")
    agent_commits = 0   # denominator: commits actually authored by a coding agent
    for r in q("SELECT cm.author_login, cm.committer_login, cm.co_authors, p.author "
               "FROM commits cm LEFT JOIN prs p ON p.id=cm.pr_id"):
        if agents.classify_actor(r["author"], r["author_login"], r["committer_login"],
                                 r["co_authors"])[0] == "coding_agent":
            agent_commits += 1
    doc_code = 100 * agent_satd_commits / agent_commits if agent_commits else None
    au = one("SELECT COUNT(*) FROM review_events WHERE actor_type='ai' AND body IS NOT NULL") or 0
    ah = one("SELECT COUNT(*) FROM satd_instances WHERE channel='conversation' AND author_class='ai' "
             "AND source_kind IN ('agent_reply','developer_request')")
    doc_conv = 100 * ah / au if au else None
    print(f"  documents debt in CODE comments : {agent_satd_commits}/{agent_commits} agent commits = {pct(agent_satd_commits, agent_commits)}")
    print(f"  documents debt in CONVERSATION  : {ah}/{au} agent utterances = {pct(ah, au)}")

    tracked = one("SELECT COUNT(*) FROM satd_instances WHERE introducer_kind='coding_agent' AND n_ai_revisits IS NOT NULL")
    revisit_pct = None
    if tracked:
        revisited = one("SELECT COUNT(*) FROM satd_instances WHERE introducer_kind='coding_agent' AND n_ai_revisits>0")
        total_rev = one("SELECT COALESCE(SUM(n_ai_revisits),0) FROM satd_instances WHERE introducer_kind='coding_agent'")
        mean_rev = one("SELECT AVG(n_ai_revisits) FROM satd_instances WHERE introducer_kind='coding_agent' AND n_ai_revisits>0") or 0
        revisit_pct = 100 * revisited / tracked
        print(f"  revisits its OWN debt           : {revisited}/{tracked} tracked agent-introduced = {pct(revisited, tracked)}")
        print(f"    total agent revisit events={total_rev}, mean among revisited={mean_rev:.2f}")
    else:
        print("  revisits its OWN debt           : pending — re-run stage5 (needs clones) to fill n_ai_revisits")

    # genuine coding-agent debt admissions in developer conversations (filter out review bots)
    conv_byagent = Counter()
    for r in q("SELECT e.actor FROM satd_instances i JOIN review_events e ON e.id=i.utterance_id "
               "WHERE i.channel='conversation' AND i.source_kind='agent_reply'"):
        kind, name = agents.classify_actor(r["actor"])
        if kind == "coding_agent":
            conv_byagent[name or "other"] += 1
    print(f"  spots debt while answering developers: {sum(conv_byagent.values())} admissions "
          f"({', '.join(f'{k}={v}' for k, v in conv_byagent.most_common())})")

    # ---------------------------------------------------------------- graphs
    by_type_ai = Counter({r["satd_type"] or "unknown": r["n"] for r in q(
        "SELECT satd_type, COUNT(*) n FROM satd_instances WHERE introducer_kind='coding_agent' GROUP BY satd_type")})
    made = make_graphs(out, by_type_ai, by_intro, fp, res, st, km_x, km_y, km_med,
                       resolved_states, conn, (doc_code, doc_conv, revisit_pct), conv_byagent)
    conn.close()
    print(f"\n  graphs written to {out}/ : {', '.join(made) if made else '(none — install matplotlib)'}")
    print("=" * 66)


def kaplan_meier(ev):
    """ev = list of (duration, event:1/0). Returns (xs, ys, median) step-function points."""
    deaths, totals = defaultdict(int), defaultdict(int)
    for d, e in ev:
        totals[d] += 1
        deaths[d] += e
    n_risk, S = len(ev), 1.0
    xs, ys, median = [0.0], [1.0], None
    for t in sorted(totals):
        d = deaths[t]
        if d and n_risk:
            S *= 1 - d / n_risk
            xs.append(t); ys.append(S)
            if median is None and S <= 0.5:
                median = t
        n_risk -= totals[t]
    return xs, ys, median


def make_graphs(out, by_type, by_intro, fp, res, st, km_x, km_y, km_med, resolved_states, conn,
                agent_rates=None, conv_byagent=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    Path(out).mkdir(parents=True, exist_ok=True)
    made = []

    def save(fig, name):
        fig.tight_layout(); fig.savefig(Path(out) / name, dpi=130); plt.close(fig); made.append(name)

    # 1. SATD by type
    if by_type:
        order = [k for k in ("design", "bug", "refactoring") if k in by_type] + \
                [k for k in by_type if k not in ("design", "bug", "refactoring")]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(order, [by_type[k] for k in order],
               color=[TYPE_C.get(k, OTHER_C) for k in order])
        ax.set_title("AI-introduced SATD by type"); ax.set_ylabel("instances")
        for i, k in enumerate(order):
            ax.text(i, by_type[k], str(by_type[k]), ha="center", va="bottom")
        save(fig, "satd_by_type.png")

    # 2. introduced-by vs resolved-by — restrict to the tracked code-comment set so both
    #    sides share a denominator (conversational instances aren't tracked/resolved).
    res_map = {r["cls"]: r["n"] for r in res}
    intro_cc = {}
    for r in conn.execute("SELECT introducer_kind, COUNT(*) n FROM satd_instances "
                          "WHERE channel='code_comment' AND introducer_kind IN ('coding_agent','human') "
                          "GROUP BY introducer_kind"):
        intro_cc["ai" if r["introducer_kind"] == "coding_agent" else "human"] = r["n"]
    cats = ["ai", "human"]
    fig, ax = plt.subplots(figsize=(6, 4))
    x = range(len(cats)); w = 0.38
    ax.bar([i - w / 2 for i in x], [intro_cc.get(c, 0) for c in cats], w, label="introduced", color=OTHER_C)
    ax.bar([i + w / 2 for i in x], [res_map.get(c, 0) for c in cats], w, label="resolved",
           color=[AI_C, HUMAN_C])
    ax.set_xticks(list(x)); ax.set_xticklabels(cats)
    ax.set_title("Code-comment SATD: introduced vs resolved, by actor")
    ax.set_ylabel("instances"); ax.legend()
    save(fig, "introduced_vs_resolved.png")

    # 2b. conversational admission rate by actor (the novel finding)
    cr = []
    for at in ("ai", "human"):
        utt = conn.execute("SELECT COUNT(*) FROM review_events WHERE actor_type=? AND body IS NOT NULL",
                           (at,)).fetchone()[0] or 1
        hit = conn.execute("SELECT COUNT(*) FROM satd_instances WHERE channel='conversation' AND author_class=? "
                          "AND source_kind IN ('agent_reply','developer_request')", (at,)).fetchone()[0]
        cr.append(100 * hit / utt)
    fig, ax = plt.subplots(figsize=(5.5, 4))
    bars = ax.bar(["AI", "human"], cr, color=[AI_C, HUMAN_C])
    ax.set_title("Conversational SATD rate (% of utterances)"); ax.set_ylabel("% admitting debt")
    for b, v in zip(bars, cr):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}%", ha="center", va="bottom")
    save(fig, "conversational_rate.png")

    # 3. who spots AI-introduced debt FIRST — AI self-admission vs developer (reviewer), as % share
    one = lambda s: conn.execute(s).fetchone()[0]
    AI = "introducer_kind='coding_agent'"
    ai_first = one(f"SELECT COUNT(*) FROM satd_instances WHERE {AI} AND first_pointer_class='ai'")
    dev_first = one(f"SELECT COUNT(*) FROM satd_instances WHERE {AI} AND first_pointer_class='human'")
    det = ai_first + dev_first
    if det:
        share = [100 * ai_first / det, 100 * dev_first / det]
        fig, ax = plt.subplots(figsize=(6, 4.2))
        bars = ax.bar(["AI\n(self-admitted)", "Developer\n(spotted first)"], share, color=[AI_C, HUMAN_C])
        ax.set_title(f"Who spots AI-introduced debt first?  (n={det})")
        ax.set_ylabel("% of AI debt (first-noticer known)"); ax.set_ylim(0, 115)
        for b, v, nn in zip(bars, share, [ai_first, dev_first]):
            inside = v > 90
            ax.text(b.get_x() + b.get_width() / 2, v - 8 if inside else v + 1.5,
                    f"{v:.1f}%\n(n={nn})", ha="center", va="top" if inside else "bottom",
                    color="white" if inside else "black")
        save(fig, "first_pointer.png")

    # 4. status / success — drop 'untracked' (the untracked conversational bulk) so the
    #    actual resolution outcomes are legible.
    if st:
        items = [(k, v) for k, v in st.most_common() if k != "untracked"]
        fig, ax = plt.subplots(figsize=(7, 4))
        colr = {"resolved": "#00a884", "self_resolved_premerge": "#55c9a8",
                "comment_deleted_only": "#f0a202", "alive": "#3b7dd8",
                "file_deleted": "#d63031", "untrackable": OTHER_C}
        ax.barh([k for k, _ in items][::-1], [v for _, v in items][::-1],
                color=[colr.get(k, OTHER_C) for k, _ in items][::-1])
        ax.set_title("AI-introduced debt: resolution status"); ax.set_xlabel("instances")
        save(fig, "status_success.png")

    # 5. KM survival curve
    if km_x and len(km_x) > 1:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.step(km_x, km_y, where="post", color=AI_C, lw=2)
        ax.set_ylim(0, 1); ax.set_xlabel("days since merge"); ax.set_ylabel("surviving (unresolved) fraction")
        ax.set_title("AI-introduced SATD survival (Kaplan–Meier)")
        if km_med is not None:
            ax.axhline(0.5, ls=":", c="grey"); ax.axvline(km_med, ls=":", c="grey")
            ax.text(km_med, 0.52, f" median {km_med:.0f}d", color="grey")
        save(fig, "survival_km.png")

    # 5b. who resolves AI-introduced debt
    one = lambda s: conn.execute(s).fetchone()[0]
    AI = "introducer_kind='coding_agent'"
    premerge = one(f"SELECT COUNT(*) FROM satd_instances WHERE {AI} AND status='self_resolved_premerge'")
    ai_fix = one(f"SELECT COUNT(*) FROM satd_instances WHERE {AI} AND status IN ('resolved','file_deleted') AND resolver_class='ai'")
    hum_fix = one(f"SELECT COUNT(*) FROM satd_instances WHERE {AI} AND status IN ('resolved','file_deleted') AND resolver_class='human'")
    tot = premerge + ai_fix + hum_fix
    if tot:
        labels = ["self-resolved\npre-merge", "human\n(later)", "AI agent\n(later)"]
        vals = [premerge, hum_fix, ai_fix]
        share = [100 * v / tot for v in vals]
        fig, ax = plt.subplots(figsize=(6, 4.2))
        bars = ax.bar(labels, share, color=[OTHER_C, HUMAN_C, AI_C])
        ax.set_title(f"Who resolves AI-introduced technical debt?  (n={tot})")
        ax.set_ylabel("% of resolved AI debt"); ax.set_ylim(0, 100)
        for b, v, pc in zip(bars, vals, share):
            ax.text(b.get_x() + b.get_width() / 2, pc, f"{pc:.0f}%\n(n={v})", ha="center", va="bottom")
        ax.margins(y=0.15)
        save(fig, "resolver_of_ai_debt.png")

    # 5c. AI spotting debt while answering developers (conversational channel, by agent)
    if conv_byagent:
        order = [k for k, _ in conv_byagent.most_common()]
        tot_c = sum(conv_byagent.values())
        fig, ax = plt.subplots(figsize=(6.5, 4))
        bars = ax.bar(order, [conv_byagent[k] for k in order], color=AI_C)
        ax.set_title(f"AI spotting technical debt in developer conversations  (n={tot_c})")
        ax.set_ylabel("debt admissions")
        for b, k in zip(bars, order):
            ax.text(b.get_x() + b.get_width() / 2, conv_byagent[k], str(conv_byagent[k]), ha="center", va="bottom")
        ax.margins(y=0.12)
        save(fig, "ai_spots_debt_in_conversation.png")

    # 6. agent behaviour: documents in code / in conversation / revisits own debt
    if agent_rates:
        labels = ["documents\nin code", "documents\nin conversation", "revisits\nown debt"]
        vals = [v if v is not None else 0 for v in agent_rates]
        fig, ax = plt.subplots(figsize=(6, 4))
        bars = ax.bar(labels, vals, color=[AI_C, "#f0a202", "#6c5ce7"])
        ax.set_title("Coding agents and their own technical debt"); ax.set_ylabel("%")
        for b, v, raw in zip(bars, vals, agent_rates):
            ax.text(b.get_x() + b.get_width() / 2, v, "n/a" if raw is None else f"{v:.1f}%",
                    ha="center", va="bottom")
        save(fig, "agent_behavior.png")

    return made


def write_csv(db, path):
    """Structured headline metrics -> CSV (section, metric, value) for the writeup."""
    import csv
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    one = lambda s, p=(): conn.execute(s, p).fetchone()[0]
    rows = []
    add = lambda sec, m, v: rows.append((sec, m, v))
    rs = ("resolved", "self_resolved_premerge"); ph = ",".join("?" * len(rs))

    total = one("SELECT COUNT(*) FROM satd_instances")
    add("corpus", "total_instances", total)
    for grp, col in (("channel", "channel"), ("type", "satd_type"), ("introduced_by", "author_class")):
        for r in conn.execute(f"SELECT COALESCE({col},'unknown') k, COUNT(*) n FROM satd_instances GROUP BY k"):
            add("corpus", f"{grp}.{r['k']}", r["n"])

    ncom = one("SELECT COUNT(DISTINCT sha) FROM commits") or 0
    cc = one("SELECT COUNT(DISTINCT introducing_sha) FROM satd_instances WHERE channel='code_comment'")
    add("detection", "code_comment_commits_with_satd", cc)
    add("detection", "total_commits", ncom)
    add("detection", "code_comment_rate_pct", round(100 * cc / ncom, 2) if ncom else "")
    for at in ("ai", "human"):   # per-actor code-comment rate (AI vs developer admission in code)
        com = one("SELECT COUNT(DISTINCT sha) FROM commits WHERE author_class=?", (at,)) or 0
        ccx = one("SELECT COUNT(DISTINCT introducing_sha) FROM satd_instances "
                  "WHERE channel='code_comment' AND author_class=?", (at,))
        add("detection", f"code_comment_{at}_commits_with_satd", ccx)
        add("detection", f"code_comment_{at}_commits", com)
        add("detection", f"code_comment_{at}_rate_pct", round(100 * ccx / com, 2) if com else "")
    for at in ("ai", "human"):
        utt = one("SELECT COUNT(*) FROM review_events WHERE actor_type=? AND body IS NOT NULL", (at,)) or 0
        hit = one("SELECT COUNT(*) FROM satd_instances WHERE channel='conversation' AND author_class=? "
                  "AND source_kind IN ('agent_reply','developer_request')", (at,))
        add("detection", f"conversational_{at}_hits", hit)
        add("detection", f"conversational_{at}_utterances", utt)
        add("detection", f"conversational_{at}_rate_pct", round(100 * hit / utt, 2) if utt else "")

    fp = conn.execute("SELECT first_pointer_class cls, first_pointer_level lvl, COUNT(*) n FROM satd_instances "
                      "WHERE first_pointer_class IN ('ai','human') AND introducer_kind IN ('coding_agent','human') "
                      "GROUP BY cls, lvl").fetchall()
    add("first_pointer", "total", sum(r["n"] for r in fp))
    for r in fp:
        add("first_pointer", f"{r['cls']}.{r['lvl']}", r["n"])

    n_res = one(f"SELECT COUNT(*) FROM satd_instances WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human')", rs)
    add("resolved", "total", n_res)
    for r in conn.execute(f"SELECT COALESCE(resolver_class,'unknown') k, COUNT(*) n FROM satd_instances "
                          f"WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') GROUP BY k", rs):
        add("resolved", f"resolver.{r['k']}", r["n"])
    hr = one(f"SELECT COUNT(*) FROM satd_instances WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') AND resolver_class='human'", rs)
    with_ai = one(f"SELECT COUNT(*) FROM satd_instances WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') AND resolver_class='human' "
                  f"AND (COALESCE(resolver_ai_commits,0)>0 OR COALESCE(resolver_ai_discussion,0)>0)", rs)
    avg_c = one(f"SELECT AVG(COALESCE(resolver_ai_commits,0)) FROM satd_instances WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') "
                f"AND resolver_class='human'", rs) or 0
    avg_d = one(f"SELECT AVG(COALESCE(resolver_ai_discussion,0)) FROM satd_instances WHERE status IN ({ph}) AND introducer_kind IN ('coding_agent','human') "
                f"AND resolver_class='human'", rs) or 0
    add("human_resolved", "total", hr)
    add("human_resolved", "with_ai_involvement", with_ai)
    add("human_resolved", "with_ai_pct", round(100 * with_ai / hr, 2) if hr else "")
    add("human_resolved", "avg_ai_commits", round(avg_c, 3))
    add("human_resolved", "avg_ai_discussion_turns", round(avg_d, 3))

    for r in conn.execute("SELECT COALESCE(status,'untracked') k, COUNT(*) n FROM satd_instances "
                          "WHERE introducer_kind IN ('coding_agent','human') GROUP BY k"):
        add("status", r["k"], r["n"])
    silent = one("SELECT COUNT(*) FROM satd_instances WHERE status='comment_deleted_only' "
                 "AND introducer_kind IN ('coding_agent','human')")
    add("success", "genuine_resolutions", n_res)
    add("success", "silent_deletions", silent)
    add("success", "success_rate_pct", round(100 * n_res / (n_res + silent), 2) if (n_res + silent) else "")

    durs = conn.execute("SELECT survival_days d, status FROM satd_instances "
                        "WHERE survival_days IS NOT NULL AND introducer_kind='coding_agent'").fetchall()
    ev = [(r["d"], 1 if r["status"] in rs else 0) for r in durs]
    res_days = sorted(d for d, e in ev if e)
    cens = sum(1 for _, e in ev if not e)
    km_med = kaplan_meier(ev)[2] if ev else None
    add("survival", "n_with_survival_days", len(ev))
    add("survival", "events", len(res_days))
    add("survival", "censored", cens)
    add("survival", "median_resolved_days", round(res_days[len(res_days) // 2], 1) if res_days else "")
    add("survival", "km_median_days", km_med if km_med is not None else "")

    # agents documenting & revisiting their own debt
    agent_satd_commits = one("SELECT COUNT(DISTINCT introducing_sha) FROM satd_instances WHERE introducer_kind='coding_agent'")
    agent_commits = 0
    for r in conn.execute("SELECT cm.author_login, cm.committer_login, cm.co_authors, p.author "
                          "FROM commits cm LEFT JOIN prs p ON p.id=cm.pr_id"):
        if agents.classify_actor(r["author"], r["author_login"], r["committer_login"], r["co_authors"])[0] == "coding_agent":
            agent_commits += 1
    add("agent_documentation", "code_satd_commits", agent_satd_commits)
    add("agent_documentation", "coding_agent_commits", agent_commits)
    add("agent_documentation", "code_doc_rate_pct", round(100 * agent_satd_commits / agent_commits, 2) if agent_commits else "")
    au = one("SELECT COUNT(*) FROM review_events WHERE actor_type='ai' AND body IS NOT NULL") or 0
    ah = one("SELECT COUNT(*) FROM satd_instances WHERE channel='conversation' AND author_class='ai' "
             "AND source_kind IN ('agent_reply','developer_request')")
    add("agent_documentation", "conversation_doc_rate_pct", round(100 * ah / au, 2) if au else "")
    tracked = one("SELECT COUNT(*) FROM satd_instances WHERE introducer_kind='coding_agent' AND n_ai_revisits IS NOT NULL")
    revisited = one("SELECT COUNT(*) FROM satd_instances WHERE introducer_kind='coding_agent' AND n_ai_revisits>0")
    total_rev = one("SELECT COALESCE(SUM(n_ai_revisits),0) FROM satd_instances WHERE introducer_kind='coding_agent'")
    add("agent_revisit", "tracked_agent_introduced", tracked)
    add("agent_revisit", "revisited_own_debt", revisited)
    add("agent_revisit", "revisit_rate_pct", round(100 * revisited / tracked, 2) if tracked else "")
    add("agent_revisit", "total_revisit_events", total_rev)

    conn.close()
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["section", "metric", "value"]); w.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    import contextlib
    pkg = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--out", default=str(pkg / "graphs"))
    ap.add_argument("--txt", default=str(pkg / "report.txt"))
    ap.add_argument("--csv", default=str(pkg / "report_metrics.csv"))
    a = ap.parse_args()
    with open(a.txt, "w") as txtf, contextlib.redirect_stdout(_Tee(sys.stdout, txtf)):
        main(a.db, a.out)
    n = write_csv(a.db, a.csv)
    print(f"  text report -> {a.txt}\n  metrics CSV ({n} rows) -> {a.csv}")
