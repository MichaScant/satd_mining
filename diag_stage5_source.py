"""Decide how to anchor AIDev stage-5 without the flaky live API. Two questions:
  A) WHY does the PR API 404 — token scope, or PR genuinely gone? (prints raw status codes)
  B) Does AIDev's pull_request.parquet carry merge_commit_sha / merged_at / state we can use locally?
Needs GITHUB_TOKEN for part A. Read-only."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from stage2_fetch import _get

REPO, PR = "0dotxyz/marginfi-v2", 378

print("=== A) token / endpoint check ===")
print("GITHUB_TOKEN set:", bool(os.environ.get("GITHUB_TOKEN")))
for label, url in (
    ("rate_limit", f"{config.GITHUB_API}/rate_limit"),
    ("repo",       f"{config.GITHUB_API}/repos/{REPO}"),
    ("pull",       f"{config.GITHUB_API}/repos/{REPO}/pulls/{PR}"),
):
    code, body = _get(url)
    msg = ""
    if isinstance(body, dict):
        msg = body.get("message") or body.get("full_name") or (f"merge_commit_sha={body.get('merge_commit_sha')}"
                                                               if "merge_commit_sha" in body else "")
    print(f"  {label:11} code={code}  {msg}")

print("\n=== B) AIDev pull_request.parquet schema ===")
import polars as pl
f = Path(config.AIDEV_DIR if hasattr(config, "AIDEV_DIR") else "data/aidev") / "pull_request.parquet"
if not f.exists():
    f = Path("data/aidev/pull_request.parquet")
print("file:", f, "exists:", f.exists())
if f.exists():
    schema = pl.scan_parquet(f).collect_schema()
    names = schema.names()
    print("columns:", names)
    hits = [c for c in names if any(k in c.lower() for k in ("merge", "sha", "state", "merged", "closed", "head", "base"))]
    print("merge/sha/state-ish columns:", hits)
    # sample the specific PR
    want = [c for c in ("number", "state", "merged_at", "merge_commit_sha", "closed_at",
                        "head_sha", "base_sha", "merge_commit", "html_url") if c in names]
    row = (pl.scan_parquet(f).filter(pl.col("html_url").str.contains(f"/{REPO}/pull/{PR}"))
           .select(want).collect() if "html_url" in names else None)
    if row is not None and row.height:
        print("sample row for", REPO, "#", PR, ":", row.to_dicts()[0])
    else:
        print("(couldn't isolate the sample PR by html_url; columns above are what matters)")
