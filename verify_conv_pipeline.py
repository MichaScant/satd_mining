"""Sanity-check the conversation pipeline (stage2_aidev reviews -> stage3b -> stage3c -> stage5b)."""
import sqlite3
from pathlib import Path

c = sqlite3.connect(str(Path(__file__).resolve().parent / "db" / "satd_mining.db"))
def q(sql): return c.execute(sql).fetchall()
A = "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr WHERE p.cohort='aidev'"

print("=== 1. AIDev review_events loaded ===")
print("  total:", q("SELECT COUNT(*) FROM review_events e JOIN prs p ON p.id=e.pr_id WHERE p.cohort='aidev'")[0][0])
print("  by kind:", dict(q("SELECT e.kind,COUNT(*) FROM review_events e JOIN prs p ON p.id=e.pr_id WHERE p.cohort='aidev' GROUP BY e.kind")))
print("  inline w/ position:", q("SELECT COUNT(*) FROM review_events e JOIN prs p ON p.id=e.pr_id WHERE p.cohort='aidev' AND e.position IS NOT NULL")[0][0])
print("  review_state:", dict(q("SELECT e.review_state,COUNT(*) FROM review_events e JOIN prs p ON p.id=e.pr_id WHERE p.cohort='aidev' AND e.review_state IS NOT NULL GROUP BY e.review_state")))

print("=== 2. examined-PR manifest ===")
try:
    print("  aidev_sample_prs:", q("SELECT COUNT(*) FROM aidev_sample_prs")[0][0])
except Exception as e:
    print("  aidev_sample_prs MISSING:", e)

print("=== 3. AIDev conversation SATD by source_kind (stage3b, scoped) ===")
print("  ", dict(q(f"SELECT i.source_kind,COUNT(*) FROM satd_instances i {A} AND i.channel='conversation' GROUP BY i.source_kind")))

print("=== 4. who-first (stage3c) aidev ===")
print("  ", dict(q(f"SELECT i.first_pointer_class,COUNT(*) FROM satd_instances i {A} AND i.first_pointer_class IS NOT NULL GROUP BY i.first_pointer_class")))

print("=== 5. conversation resolution (stage5b) aidev ===")
print("  ", dict(q(f"SELECT i.status,COUNT(*) FROM satd_instances i {A} AND i.channel='conversation' GROUP BY i.status")))

print("=== 6. scoping check: aidev conversation PRs all within the examined sample? ===")
try:
    leaked = q(f"SELECT COUNT(*) FROM satd_instances i {A} AND i.channel='conversation' "
               "AND NOT EXISTS (SELECT 1 FROM aidev_sample_prs s WHERE s.repo=i.repo AND s.pr_number=i.introducing_pr)")[0][0]
    print("  out-of-sample conversation instances (should be 0):", leaked)
except Exception as e:
    print("  (skip)", e)
c.close()
