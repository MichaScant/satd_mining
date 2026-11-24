"""Pull real, citable examples for each detection step (for the methodology doc)."""
import sqlite3, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import satd_detect as sd
c = sqlite3.connect("db/satd_mining.db"); c.row_factory = sqlite3.Row
def clip(s, n=150): return re.sub(r"\s+", " ", s or "")[:n]

def section(t): print("\n" + "#" * 92 + f"\n## {t}\n" + "#" * 92)

section("1. SPONTANEOUS agent-spotted (how_spotted='spontaneous') — bot-opened PR, no dev prompt")
for r in c.execute("""SELECT i.repo, i.introducing_pr pr, i.channel ch, i.keyword kw, p.author,
  substr(i.comment_text_raw,1,140) t FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr
  WHERE p.cohort='aidev' AND i.spotter='agent' AND i.how_spotted='spontaneous' AND i.channel='code_comment' LIMIT 3"""):
    print(f"  {r['repo']}#{r['pr']} author={r['author']} matched_keyword='{r['kw']}'\n    “{clip(r['t'])}”")

import satd_detect as sd
from analyze_reviewer_elicited_satd import ERR, CONCERN, has_debt

def _adm_ts(r):
    """Reference time of the agent's admission: utterance ts for conversation, commit date for code."""
    if r["channel"] == "conversation" and r["uid"] is not None and r["uid"] >= 0:
        ev = c.execute("SELECT ts FROM review_events WHERE id=?", (r["uid"],)).fetchone()
        return ev["ts"] if ev and ev["ts"] else None
    row = c.execute("SELECT COALESCE(cm.committed_at,cm.authored_at,d.committed_at,d.authored_at) "
                    "FROM satd_instances i LEFT JOIN commits cm ON cm.sha=i.introducing_sha "
                    "LEFT JOIN aidev_commit_dates d ON d.sha=i.introducing_sha WHERE i.id=?",
                    (r["id"],)).fetchone()
    return row[0] if row and row[0] else None

def _trigger_turn(pr_dbid, before_ts, actor_type, circ):
    """The earlier PR turn (by timestamp, PR-wide — not file/thread-scoped) that set this circumstance."""
    if not before_ts:
        return None
    rows = c.execute("SELECT actor, body, ts FROM review_events WHERE pr_id=? AND actor_type=? "
                     "AND ts IS NOT NULL AND ts<? AND body IS NOT NULL ORDER BY ts, id",
                     (pr_dbid, actor_type, before_ts)).fetchall()
    for t in rows:
        b = sd.strip_code(t["body"] or "")
        if "named" in circ and (has_debt(b) or (actor_type == "ai_reviewer" and ERR.search(b))):
            return t
        if "alluded" in circ and ERR.search(b):
            return t
        if "concern" in circ and CONCERN.search(b):
            return t
    return rows[0] if rows else None

_EX = ("SELECT i.id, i.channel, i.repo, i.introducing_pr pr, i.utterance_id uid, i.circumstance circ, "
       "p.id pr_dbid, p.author author, substr(i.comment_text_raw,1,150) t "
       "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
       "WHERE p.cohort='aidev' AND i.spotter='agent' ")

section("2. DEV-ELICITED (how_spotted='dev_elicited') — triggering dev message shown first")
print("-- (a) in-thread developer signal (a dev turn preceded the admission):")
for r in c.execute(_EX + "AND i.how_spotted='dev_elicited' AND i.circumstance IN "
                   "('dev named debt','dev alluded to error','dev raised concern/question') GROUP BY i.introducing_pr LIMIT 3"):
    trig = _trigger_turn(r["pr_dbid"], _adm_ts(r), "human", r["circ"])
    print(f"\n  [{r['repo']}#{r['pr']}] circumstance='{r['circ']}'")
    print(f"    DEV (trigger, earlier) : “{clip(trig['body']) if trig else '(ts unresolved)'}”")
    print(f"    AGENT (admission)      : “{clip(r['t'])}”")
print("\n-- (b) developer-initiated PR (human opened/assigned the PR; no in-thread comment needed):")
for r in c.execute(_EX + "AND i.how_spotted='dev_elicited' AND i.circumstance='developer-initiated PR' GROUP BY i.introducing_pr LIMIT 3"):
    print(f"\n  [{r['repo']}#{r['pr']}]")
    print(f"    DEV (trigger)          : PR opened by human developer '{r['author']}' (directed the agent)")
    print(f"    AGENT (admission)      : “{clip(r['t'])}”")

section("3. REVIEW-BOT ELICITED (how_spotted='reviewbot_elicited') — triggering review-bot message first")
for r in c.execute(_EX + "AND i.how_spotted='reviewbot_elicited' GROUP BY i.introducing_pr LIMIT 3"):
    trig = _trigger_turn(r["pr_dbid"], _adm_ts(r), "ai_reviewer", r["circ"])
    print(f"\n  [{r['repo']}#{r['pr']}] circumstance='{r['circ']}'")
    print(f"    REVIEW-BOT (trigger)   : [{trig['actor'] if trig else '?'}] “{clip(trig['body']) if trig else '(ts unresolved)'}”")
    print(f"    AGENT (admission)      : “{clip(r['t'])}”")

section("4. REVIEWBOT-SPOTTED posts routed OUT of agent channel (spotter='reviewbot', under agent acct)")
for r in c.execute("""SELECT i.repo, i.introducing_pr pr, substr(i.comment_text_raw,1,130) t
  FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr
  WHERE p.cohort='aidev' AND i.source_kind='ai_reviewer_flag' AND i.channel='conversation' LIMIT 3"""):
    print(f"  {r['repo']}#{r['pr']}  is_review_bot_post=True\n    “{clip(r['t'])}”")

section("5. RESOLUTION — code removal (git pickaxe), with the resolving commit")
for r in c.execute("""SELECT repo, introducing_pr pr, resolved_how, substr(resolved_sha,1,12) sha, substr(comment_text_raw,1,110) t
  FROM satd_instances WHERE outcome='resolved' AND resolved_how='code_removal' AND resolved_sha IS NOT NULL LIMIT 3"""):
    print(f"  {r['repo']}#{r['pr']} how={r['resolved_how']} removing_commit={r['sha']}\n    “{clip(r['t'])}”")

section("6. RESOLUTION — conversation immediate_fix / later_confirmation")
for r in c.execute("""SELECT i.repo repo, i.introducing_pr pr, resolved_how, resolver, substr(comment_text_raw,1,110) t
  FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr
  WHERE p.cohort='aidev' AND resolved_how IN ('immediate_fix','later_confirmation') LIMIT 4"""):
    print(f"  {r['repo']}#{r['pr']} how={r['resolved_how']} resolver={r['resolver']}\n    “{clip(r['t'])}”")

section("7. ISSUE channel — valid PR<->issue links referencing debt (closed_by_merge)")
for r in c.execute("""SELECT repo, pr_number, issue_repo, issue_number, intent, issue_debt, closed_by_merge, state,
  substr(snippet,1,130) snip FROM aidev_issue_links WHERE valid=1 LIMIT 6"""):
    print(f"  PR {r['repo']}#{r['pr_number']}  ->  ISSUE {r['issue_repo']}#{r['issue_number']}  "
          f"intent={r['intent']} issue_debt={r['issue_debt']} closed_by_merge={r['closed_by_merge']} state={r['state']}")
    print(f"     reference context: “{clip(r['snip'])}”")

section("8. RESOLUTION via issue (resolved_how='issue_closed')")
for r in c.execute("""SELECT i.repo repo, i.introducing_pr pr, i.resolved_sha, substr(i.comment_text_raw,1,110) t
  FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr
  WHERE p.cohort='aidev' AND i.resolved_how='issue_closed' LIMIT 3"""):
    print(f"  {r['repo']}#{r['pr']} tracked_issue={r['resolved_sha']}\n    “{clip(r['t'])}”")
c.close()
