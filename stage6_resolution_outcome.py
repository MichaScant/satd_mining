"""Stage 6 — unified per-SATD resolution OUTCOME (the RQ2 answer for every declared debt).

For each declared AIDev SATD (code_comment + conversation) produce ONE auditable outcome:

  outcome      resolved | dropped | untrackable
  resolved_how code_removal | immediate_fix | issue_closed        (NULL unless resolved)
  resolver     ai | human | issue_tracker
  resolved_when ISO timestamp, anchored to a real commit/merge/close
  n_revisits   AI-authored commits to the RESOLVING PR before resolved_when

Attribution is INSTANCE-LEVEL (v2) — a PR-wide signal is NOT spread across every conversation SATD
in that PR (the v1 bug that inflated issue_closed to 180 vs 94 real closed issues):

CODE comment (git, stage5): unchanged — resolved iff status in the removed-set; revisits = AI commits
  in the SATD's own PR before resolved_at.

CONVERSATION (no sha; two instance-tied pathways only):
  (B) issue    a VALID issue link that THIS instance raised (exact utterance_id, or pr_body<->pr_body)
               and the issue is closed — gated on closed_by_merge when stage3f has run (else state=closed).
               resolved_when = issue closed_at; revisits = stage3f's AI-commits-to-closing-PR.
  (A) immediate a same-FILE AI removal confirmation (is_debt_removal on the SAME file_path as the
               admission — not a PR-wide "fixed it"). General PR-comment debt (no file) can't be
               file-scoped, so it does NOT get immediate_fix. resolved_when = latest commit in the PR
               at/<= the confirmation ts; resolver = ai if an agent authored it else human.
  dropped      otherwise if the PR is unmerged/abandoned.
  untrackable  otherwise (merged, no instance-tied resolution signal).

AI commit = author_login is a coding-agent account (agents.agent_name) — the Suzhen rule.

  python stage6_resolution_outcome.py
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import satd_detect as sd
from db import store

_RES_CODE = {"resolved", "file_deleted", "comment_deleted_only", "self_resolved_premerge"}


def _add_cols(conn):
    for col, decl in (("outcome", "TEXT"), ("resolved_how", "TEXT"), ("resolver", "TEXT"),
                      ("resolved_when", "TEXT"), ("n_revisits_resolve", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE satd_instances ADD COLUMN {col} {decl}")
        except Exception:
            pass
    conn.commit()


def _has_col(conn, table, col):
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def run():
    conn = store.connect(); store.init_db(conn); _add_cols(conn)
    has_merge_gate = _has_col(conn, "aidev_issue_links", "closed_by_merge")
    has_issue_rev = _has_col(conn, "aidev_issue_links", "n_ai_revisits_issue")
    if not has_merge_gate:
        print("note: stage3f not run yet — issue resolution gated on state='closed' (not closed_by_merge)")

    commit_cache = {}
    def commits(pr_dbid):
        if pr_dbid not in commit_cache:
            commit_cache[pr_dbid] = conn.execute(
                "SELECT author_login, authored_at FROM commits WHERE pr_id=?", (pr_dbid,)).fetchall()
        return commit_cache[pr_dbid]

    def ai_revisits(pr_dbid, when):
        return sum(1 for r in commits(pr_dbid)
                   if r["author_login"] and agents.agent_name(r["author_login"])
                   and r["authored_at"] and (not when or r["authored_at"] <= when))

    def latest_commit_before(pr_dbid, ts):
        cand = [r["authored_at"] for r in commits(pr_dbid) if r["authored_at"] and (not ts or r["authored_at"] <= ts)]
        return max(cand) if cand else None

    # per-PR earliest AI removal-confirmation ts, keyed by FILE (file_path or __pr__/N, matching satd path)
    conf_cache = {}
    def confirm_ts_by_file(pr_dbid, pr_number):
        if pr_dbid not in conf_cache:
            d = {}
            for r in conn.execute("SELECT body, ts, file_path FROM review_events WHERE pr_id=? "
                                  "AND actor_type='ai' AND body IS NOT NULL", (pr_dbid,)):
                if r["ts"] and sd.is_debt_removal(r["body"]):
                    path = r["file_path"] or f"__pr__/{pr_number}"
                    if path not in d or r["ts"] < d[path]:
                        d[path] = r["ts"]
            conf_cache[pr_dbid] = d
        return conf_cache[pr_dbid]

    # instance-tied closed issue: exact utterance, or pr_body<->pr_body. Returns (closed_at, revisits).
    gate = "AND closed_by_merge=1" if has_merge_gate else ""
    revcol = "n_ai_revisits_issue" if has_issue_rev else "NULL"
    def issue_resolution(repo, pr, utt, src):
        if utt is not None and utt >= 0:
            r = conn.execute(f"SELECT closed_at, {revcol} rv FROM aidev_issue_links WHERE valid=1 "
                             f"AND state='closed' AND closed_at IS NOT NULL {gate} AND utterance_id=?",
                             (utt,)).fetchone()
            if r:
                return r["closed_at"], r["rv"]
        if src == "pr_body":
            r = conn.execute(f"SELECT closed_at, {revcol} rv FROM aidev_issue_links WHERE valid=1 "
                             f"AND state='closed' AND closed_at IS NOT NULL {gate} AND repo=? "
                             f"AND pr_number=? AND source_kind='pr_body' ORDER BY closed_at LIMIT 1",
                             (repo, pr)).fetchone()
            if r:
                return r["closed_at"], r["rv"]
        return None, None

    # per-PR conversation turns (id, ts, actor, body) — for admission timing + later-confirmation scan
    turns_cache = {}
    def turns(pr_dbid):
        if pr_dbid not in turns_cache:
            turns_cache[pr_dbid] = conn.execute(
                "SELECT id, ts, actor_type, body FROM review_events WHERE pr_id=? AND body IS NOT NULL",
                (pr_dbid,)).fetchall()
        return turns_cache[pr_dbid]

    def admission_ts(pr_dbid, utt, pr_created):
        if utt is not None and utt >= 0:
            for t in turns(pr_dbid):
                if t["id"] == utt:
                    return t["ts"]
        return pr_created                               # pr_body admission -> PR open time

    def later_confirmation(pr_dbid, after_ts, kw):
        """Earliest later turn (agent OR dev) that confirms removal AND names the same debt term."""
        kwl = (kw or "").lower()
        best = None
        for t in turns(pr_dbid):
            if t["actor_type"] not in ("ai", "human") or not t["ts"]:
                continue
            if after_ts and t["ts"] <= after_ts:
                continue
            body = t["body"] or ""
            if sd.is_debt_removal(body) and (not kwl or kwl in body.lower()):
                if best is None or (t["ts"] and t["ts"] < best[0]):
                    best = (t["ts"], t["actor_type"])
        return best

    rows = conn.execute(
        "SELECT i.id, i.channel, i.status, i.repo, i.introducing_pr AS pr, i.utterance_id AS utt, "
        "i.path, i.source_kind, i.keyword AS kw, i.resolved_at, i.resolver_class, "
        "i.n_revisits_pre_res AS npr, i.n_ai_near_edits AS nne, "
        "p.id AS pr_dbid, p.merged_at, p.created_at AS pr_created "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel IN ('code_comment','conversation')").fetchall()

    def code_revisits(r):
        # patch-based revisit count (stage5c), authorship-independent — AIDev attributes commits to
        # the human/web-flow merger, so counting agent-authored commits is unreliable; near-edits are not.
        return r["npr"] if r["npr"] is not None else (r["nne"] or 0)
    print(f"classifying {len(rows)} declared AIDev SATD ...", flush=True)

    n = 0
    for r in rows:
        outcome = how = resolver = when = None
        revisits = 0
        if r["channel"] == "code_comment":
            st = r["status"]
            if st in _RES_CODE:
                outcome, how, when = "resolved", "code_removal", r["resolved_at"]
                resolver = r["resolver_class"]
                revisits = code_revisits(r)
            elif st == "untrackable":
                # comment provably existed (we detected it) and is gone from HEAD -> it was removed
                # (pre- or post-merge); only a reverted introducing commit wouldn't count, which is rare.
                # We can't pin the removing commit, so timestamp at merge (lower bound).
                outcome, how, when = "resolved", "code_removal_unpinned", r["merged_at"]
                revisits = code_revisits(r)
            else:                                   # alive (merged, still present) / pr_open / pr_unmerged
                outcome = "dropped"
        else:                                       # conversation
            iclosed, irev = issue_resolution(r["repo"], r["pr"], r["utt"], r["source_kind"])
            path = r["path"] or ""
            file_scoped = path and not path.startswith("__pr__")
            revisits = None                         # patch-based revisits are a code-SATD metric only
            if iclosed:
                outcome, how, resolver, when = "resolved", "issue_closed", "issue_tracker", iclosed
                revisits = irev                     # AI commits to the closing PR (stage3f), may be sparse
            elif file_scoped and path in confirm_ts_by_file(r["pr_dbid"], r["pr"]):
                cts = confirm_ts_by_file(r["pr_dbid"], r["pr"])[path]
                when = latest_commit_before(r["pr_dbid"], cts) or cts
                outcome, how, resolver = "resolved", "immediate_fix", "ai"
            else:
                # Point 2: before giving up, look for a LATER turn (agent or dev) that confirms this
                # debt was addressed (removal language + same debt term). If none -> exclude (no
                # observable outcome; kept for RQ1 noticing, dropped from RQ2 denominator).
                lc = later_confirmation(r["pr_dbid"], admission_ts(r["pr_dbid"], r["utt"], r["pr_created"]), r["kw"])
                if lc:
                    when, actor = lc
                    outcome, how, resolver = "resolved", "later_confirmation", actor
                elif not r["merged_at"]:
                    outcome = "dropped"             # abandoned PR -> debt died with it
                else:
                    outcome = "excluded"            # merged, no observable resolution signal

        conn.execute("UPDATE satd_instances SET outcome=?, resolved_how=?, resolver=?, "
                     "resolved_when=?, n_revisits_resolve=? WHERE id=?",
                     (outcome, how, resolver, when, revisits, r["id"]))
        n += 1
        if n % 20000 == 0:
            conn.commit(); print(f"  {n}/{len(rows)}", flush=True)
    conn.commit()
    _summary(conn)
    conn.close()


def _summary(conn):
    from stage1_aidev import AIDEV_AGENT_MAP
    canon = lambda a: AIDEV_AGENT_MAP.get(str(a or "").strip().lower(), str(a))
    agg = defaultdict(Counter); how = defaultdict(Counter)
    for ag, oc, hw, n in conn.execute(
        "SELECT i.agent_name, i.outcome, i.resolved_how, COUNT(*) "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment' GROUP BY 1,2,3"):
        agg[ag][oc] += n
        if hw: how[ag][hw] += n
    for ag0, oc, hw, n in conn.execute(
        "SELECT p.agent, i.outcome, i.resolved_how, COUNT(*) "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='conversation' GROUP BY 1,2,3"):
        ag = canon(ag0); agg[ag][oc] += n
        if hw: how[ag][hw] += n
    print("\n[per-agent outcome mix: resolved / dropped / excluded]")
    print(f"{'agent':8}{'declared':>10}{'resolved':>10}{'dropped':>10}{'excluded':>10}{'res%(track)':>12}")
    for ag in sorted(agg, key=lambda a: -sum(agg[a].values())):
        if ag in ("None", None):
            continue
        res, dr, ex = agg[ag]["resolved"], agg[ag]["dropped"], agg[ag]["excluded"]
        tot = res + dr + ex; track = res + dr          # excluded = no observable outcome (RQ1 only)
        pct = f"{100*res/track:.1f}%" if track else "—"
        print(f"{ag:8}{tot:>10}{res:>10}{dr:>10}{ex:>10}{pct:>12}")
    print("\n[how resolved, by agent]")
    for ag in sorted(how, key=lambda a: -sum(how[a].values())):
        if ag in ("None", None):
            continue
        print(f"   {ag:8} {dict(how[ag])}")


if __name__ == "__main__":
    run()
