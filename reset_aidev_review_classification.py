"""Re-apply the 3-way actor taxonomy (self_agent/ai_reviewer/human/automation) to AIDev review
events IN PLACE — no parquet re-parse — after the agents.py fix. The first load stored the wrong
convention ('coding_agent'/'automation_bot'/'human'), which stage3b maps to nothing -> agent_reply
was 0. This updates review_events.actor_type and clears AIDev-derived reviewer_flags so stage3c
rebuilds them with the corrected actor types.

Run (then re-run stage3b -> stage3c -> stage5b):
  python reset_aidev_review_classification.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
from db import store
from stage2_aidev import _ACTOR_MAP

conn = store.connect()

rows = conn.execute(
    "SELECT e.id, e.actor, p.agent FROM review_events e JOIN prs p ON p.id=e.pr_id "
    "WHERE p.cohort='aidev'").fetchall()
print(f"reclassifying {len(rows)} AIDev review events ...", flush=True)

stats = Counter()
for i, r in enumerate(rows, 1):
    atype = _ACTOR_MAP.get(agents.classify_review_actor(r["actor"], r["agent"]), "human")
    conn.execute("UPDATE review_events SET actor_type=? WHERE id=?", (atype, r["id"]))
    stats[atype] += 1
    if i % 50000 == 0:
        conn.commit(); print(f"  {i}/{len(rows)}", flush=True)
conn.commit()
print("new actor_type distribution:", dict(stats))

# clear AIDev reviewer_flags (INSERT OR IGNORE won't refresh stale actor_type) so stage3c rebuilds
d = conn.execute(
    "DELETE FROM reviewer_flags WHERE pr_id IN (SELECT id FROM prs WHERE cohort='aidev')").rowcount
conn.commit()
print(f"cleared {d} AIDev reviewer_flags (stage3c will rebuild)")
conn.close()
print("done. now re-run: stage3b_conversational.py -> stage3c_reviewer_flags.py -> stage5b_conversation_resolution.py")
