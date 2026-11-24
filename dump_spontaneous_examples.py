"""Audit SPONTANEOUS agent-spotted SATD: print the admission + every PR turn (with actor_type + login)
so we can see whether it's genuinely unprompted, and whether any REVIEW-BOT turn is present (which
must NOT be counted as spontaneous). Flags reviewbot involvement explicitly.

  python dump_spontaneous_examples.py [N]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
from db import store

N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
SPON = "SPONTANEOUS (no signal)"


def _turns(conn, pr_dbid):
    return conn.execute(
        "SELECT actor_type, actor, ts, substr(replace(body,char(10),' '),1,120) b "
        "FROM review_events WHERE pr_id=? AND body IS NOT NULL ORDER BY ts, id", (pr_dbid,)).fetchall()


def _reviewbot_in_pr(turns):
    for t in turns:
        if t["actor_type"] == "ai_reviewer" or (t["actor"] and agents.AI_REVIEWER_RE.search(t["actor"])):
            return t["actor"]
    return None


def _dump(conn, title, where_src):
    print("\n" + "=" * 80); print(title); print("=" * 80)
    rows = conn.execute(
        f"SELECT i.id, i.repo, i.introducing_pr pr, i.utterance_id uid, i.source_kind sk, p.id pr_dbid, "
        f"substr(replace(i.comment_text_raw,char(10),' '),1,180) txt "
        f"FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        f"WHERE p.cohort='aidev' AND i.circumstance=? AND ({where_src}) ORDER BY i.id LIMIT ?",
        (SPON, N)).fetchall()
    for r in rows:
        turns = _turns(conn, r["pr_dbid"])
        adm_ts = None
        if r["uid"] is not None and r["uid"] >= 0:
            ev = conn.execute("SELECT ts FROM review_events WHERE id=?", (r["uid"],)).fetchone()
            adm_ts = ev["ts"] if ev else None
        rb = _reviewbot_in_pr(turns)
        print(f"\n[{r['repo']} #{r['pr']}] src={r['sk']} adm_ts={adm_ts}"
              + (f"   ⚠ REVIEWBOT IN PR: {rb}" if rb else "   (no reviewbot in PR)"))
        print(f"  ADMISSION: {r['txt']}")
        prior = [t for t in turns if adm_ts and t["ts"] and t["ts"] < adm_ts] if adm_ts else []
        if not prior:
            print("  prior turns: (none before admission)")
        else:
            print(f"  prior turns ({len(prior)}):")
            for t in prior[-6:]:
                print(f"    [{t['actor_type']:11} {str(t['actor'])[:24]:24} {str(t['ts'])}] {t['b']}")


def run():
    conn = store.connect()
    _dump(conn, "CONVERSATION agent_reply marked SPONTANEOUS", "i.source_kind='agent_reply'")
    _dump(conn, "CODE-COMMENT marked SPONTANEOUS", "i.channel='code_comment'")
    conn.close()


if __name__ == "__main__":
    run()
