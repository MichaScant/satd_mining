"""Why did stage-5 clones fail for AIDev? Show the repo_url we stored and the ACTUAL git error."""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from db import store

conn = store.connect()
rows = conn.execute(
    "SELECT i.repo, p.repo_url, i.introducing_pr, i.path FROM satd_instances i "
    "LEFT JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
    "WHERE i.status IS NOT NULL AND p.cohort='aidev' "   # the ones just marked (untrackable)
    "ORDER BY i.repo LIMIT 20"
).fetchall()

print("CLONES_DIR:", config.CLONES_DIR, "exists:", config.CLONES_DIR.exists())
seen = {}
for r in rows:
    seen.setdefault(r["repo"], r["repo_url"])
print(f"\ndistinct repos in the 20-sample: {len(seen)}")
for repo, url in seen.items():
    print(f"   repo={repo!r}  repo_url={url!r}")

# try cloning each distinct repo BOTH ways, show stderr
for repo, url in seen.items():
    print(f"\n==== {repo} ====")
    for label, u in (("stored repo_url", url), ("canonical", f"https://github.com/{repo}.git")):
        if not u:
            print(f"  [{label}] (none)"); continue
        with tempfile.TemporaryDirectory() as td:
            cp = subprocess.run(
                ["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1", u, td + "/c"],
                capture_output=True, text=True)
            err = (cp.stderr or cp.stdout or "").strip().replace("\n", " ")[:300]
            print(f"  [{label}] url={u!r} rc={cp.returncode} :: {err}")
conn.close()
