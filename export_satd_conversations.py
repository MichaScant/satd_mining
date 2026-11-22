"""Export every debt-bearing CONVERSATION for manual analysis — both cohorts, one CSV.

Two record types, unified schema:
  conv_satd     — conversational SATD instances (channel='conversation'):
                    source_kind = pr_body | agent_reply | developer_request
                  i.e. the PR author / agent / developer ADMITTING or declaring debt in words.
  reviewer_flag — a reviewer pointing out debt the author did NOT self-admit (reviewer_flags);
                  kept OUT of the self-admitted SATD counts by definition, but exported here
                  because reviewer-spotted -> AI-repaired is a valid "developer helped" instance.

The full utterance text is included so the debt mention can be read in context. AIDev rows are
already scoped to the examined sample by stage3b/stage3c, so this reflects the same PRs as the
code scan.

  python export_satd_conversations.py [out.csv]      # default: satd_conversations_for_review.csv
"""
import csv
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent / "db" / "satd_mining.db"
OUT = sys.argv[1] if len(sys.argv) > 1 else "satd_conversations_for_review.csv"

COLS = ["record_type", "cohort", "agent_name", "repo", "pr_number", "actor_type",
        "source_kind_or_category", "satd_type", "keyword", "level", "text"]


def main():
    c = sqlite3.connect(str(DB)); c.row_factory = sqlite3.Row
    rows = []

    # 1) conversational SATD admissions (author/agent/developer)
    for r in c.execute(
        "SELECT p.cohort, COALESCE(i.agent_name, p.agent) agent_name, i.repo, i.introducing_pr pr, "
        "       i.author_class, i.source_kind, i.satd_type, i.keyword, i.comment_text_raw txt "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE i.channel='conversation'"):
        rows.append(["conv_satd", r["cohort"], r["agent_name"], r["repo"], r["pr"],
                     r["author_class"], r["source_kind"], r["satd_type"], r["keyword"], "", r["txt"]])

    # 2) reviewer flags (debt a reviewer pointed out) — text pulled from the review event
    for r in c.execute(
        "SELECT p.cohort, p.agent agent_name, p.repo, p.pr_number pr, rf.actor_type, rf.category, "
        "       rf.level, e.body txt "
        "FROM reviewer_flags rf JOIN prs p ON p.id=rf.pr_id "
        "LEFT JOIN review_events e ON e.id=rf.event_id"):
        rows.append(["reviewer_flag", r["cohort"], r["agent_name"], r["repo"], r["pr"],
                     r["actor_type"], r["category"], "", "", r["level"],
                     (r["txt"] or "")[:1000]])

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f); w.writerow(COLS); w.writerows(rows)

    # summary
    from collections import Counter
    byrt = Counter(r[0] for r in rows)
    bycohort = Counter((r[0], r[1]) for r in rows)
    print(f"wrote {OUT}: {len(rows)} rows")
    print("  by record_type:", dict(byrt))
    print("  by (record_type, cohort):", dict(bycohort))
    c.close()


if __name__ == "__main__":
    main()
