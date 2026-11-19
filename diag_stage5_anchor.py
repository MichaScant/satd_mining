"""Why did every AIDev instance land 'self_resolved_premerge'? Inspect the merge anchor:
what GitHub returns for the PR (state/merged/merge_commit_sha) and whether the intro + merge
SHAs are actually in the clone and contain the snippet. Needs GITHUB_TOKEN. Read-only."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from db import store
from stage2_fetch import _get

print("GITHUB_TOKEN set:", bool(os.environ.get("GITHUB_TOKEN")),
      "len:", len(os.environ.get("GITHUB_TOKEN", "")))

conn = store.connect()
rows = conn.execute(
    "SELECT i.id, i.repo, i.introducing_pr, i.introducing_sha, i.path, "
    "       i.comment_text_raw, i.comment_text_norm, p.repo_url, p.state, p.merged_at "
    "FROM satd_instances i LEFT JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
    "WHERE p.cohort='aidev' AND i.status='self_resolved_premerge' ORDER BY i.repo LIMIT 6"
).fetchall()


def git(clone, *a):
    return subprocess.run(["git", "-C", str(clone), *a], capture_output=True, text=True)


for r in rows:
    repo, pr, isha, path = r["repo"], r["introducing_pr"], r["introducing_sha"], r["path"]
    snippet = (r["comment_text_raw"] or r["comment_text_norm"] or "").strip()
    print(f"\n==== {repo} #{pr}  path={path}")
    print(f"   stored prs.state={r['state']!r} prs.merged_at={r['merged_at']!r}")
    code, prj = _get(f"{config.GITHUB_API}/repos/{repo}/pulls/{pr}")
    if prj:
        print(f"   API: state={prj.get('state')} merged={prj.get('merged')} "
              f"merge_commit_sha={prj.get('merge_commit_sha')} merged_at={prj.get('merged_at')}")
    else:
        print(f"   API call FAILED (code={code}) -> merge_info would return (None, None)")
    clone = config.CLONES_DIR / repo.replace("/", "__")
    msha = (prj or {}).get("merge_commit_sha")
    for label, sha in (("intro", isha), ("merge", msha)):
        if not sha:
            print(f"   {label}: <no sha>"); continue
        t = git(clone, "cat-file", "-t", sha).stdout.strip()
        if t != "commit":
            print(f"   {label} {sha[:10]}: NOT in clone (cat-file -> {t!r})"); continue
        blob = git(clone, "show", f"{sha}:{path}").stdout
        print(f"   {label} {sha[:10]}: in clone, file {'present' if blob else 'MISSING'}, "
              f"snippet {'FOUND' if snippet.lower() in blob.lower() else 'absent'}")
    print(f"   snippet[:80]={snippet[:80]!r}")
conn.close()
