"""Is agent_reply=0 real (agents don't comment) or a classification bug (agent accounts -> 'human')?"""
import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents

c = sqlite3.connect(str(Path(__file__).resolve().parent / "db" / "satd_mining.db"))
def q(s): return c.execute(s).fetchall()
BASE = "FROM review_events e JOIN prs p ON p.id=e.pr_id WHERE p.cohort='aidev'"

print("=== actor_type distribution of AIDev review_events ===")
print("  ", dict(q(f"SELECT e.actor_type, COUNT(*) {BASE} GROUP BY e.actor_type")))

print("=== top 25 distinct comment authors (actor) in AIDev review_events + how classify_actor labels them ===")
rows = q(f"SELECT e.actor, COUNT(*) n {BASE} AND e.actor IS NOT NULL GROUP BY e.actor ORDER BY n DESC LIMIT 25")
for actor, n in rows:
    kind, name = agents.classify_actor(actor)
    flag = "  <-- CODING AGENT" if kind == "coding_agent" else ""
    print(f"  {str(actor):40} {n:>7}  -> {kind}/{name}{flag}")

print("=== do any AIDev PR authors (the agents) appear as comment authors? ===")
# PR authors for aidev = the agent bot logins; check if they comment
print("  distinct aidev PR authors (sample):", [r[0] for r in q(
    "SELECT DISTINCT author FROM prs WHERE cohort='aidev' AND author IS NOT NULL LIMIT 15")])
c.close()
