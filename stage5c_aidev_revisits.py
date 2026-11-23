"""Stage 5c (AIDev) — REVISIT counting: how often the agent edited the code block (the enclosing
METHOD/CLASS) around a code-comment SATD within the introducing PR, EVEN WHEN the SATD was not
removed. Fills n_ai_file_edits (same file touched in another commit) and n_ai_near_edits (same
method edited), which stage5_aidev did not compute.

Method identity = git's hunk section heading (the enclosing method/class signature git emits after
'@@ … @@'), which is robust to line shifts across commits. When a heading is absent we fall back to
a line-window overlap (±40 lines ≈ method-sized) against the SATD's introducing line.

Scope = within the introducing PR (matches Suzhen's within_pr_touches). Reads
pr_commit_details.parquet — no clones, no commit dates. Idempotent (overwrites the two counts).

  python stage5c_aidev_revisits.py --aidev-dir data/aidev
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import store
from stage1_aidev import _PRURL

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@(.*)$")
WINDOW = 40  # method-sized line window for the heading-less fallback


def hunks(patch):
    """[(new_start, new_end, heading)] for each hunk in a unified diff."""
    out = []
    for ln in (patch or "").splitlines():
        m = _HUNK.match(ln)
        if m:
            ns = int(m.group(1)); nc = int(m.group(2) or 1)
            out.append((ns, ns + max(nc, 1) - 1, (m.group(3) or "").strip()))
    return out


def run(aidev_dir="data/aidev", chunk=4000):
    d = Path(aidev_dir)
    pr = pl.read_parquet(d / "pull_request.parquet", columns=["id", "html_url"])
    rp2id = {}                                   # (repo, pr_number) -> aidev pr_id
    for row in pr.iter_rows(named=True):
        m = _PRURL.search(str(row["html_url"] or ""))
        if m:
            rp2id[(m.group(1), int(m.group(2)))] = row["id"]

    conn = store.connect(); store.init_db(conn)
    insts = conn.execute(
        "SELECT i.id, i.repo, i.introducing_pr pr, i.introducing_sha sha, i.path, i.line_span, "
        "i.resolved_at resolved_at "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment'").fetchall()
    # commit dates (from fetch_pr_commit_dates.py) enable "revisits BEFORE resolution"; absent -> skip that
    dates = {s: d for s, d in conn.execute(
        "SELECT sha, COALESCE(committed_at, authored_at) FROM aidev_commit_dates") if d}
    print(f"commit dates available for {len(dates)} shas"
          + ("" if dates else " — before-resolution counts skipped (run fetch_pr_commit_dates.py first)"),
          flush=True)
    by_pr = defaultdict(list)
    for r in insts:
        pid = rp2id.get((r["repo"], r["pr"]))
        if pid is not None:
            by_pr[pid].append(r)
    print(f"{len(insts)} AIDev code SATD across {len(by_pr)} PRs", flush=True)

    details = d / "pr_commit_details.parquet"
    pr_ids = list(by_pr)
    done = heading_anchored = window_anchored = unanchored = with_revisit = 0
    for i in range(0, len(pr_ids), chunk):
        batch = pr_ids[i:i + chunk]
        sub = (pl.scan_parquet(details).filter(pl.col("pr_id").is_in(batch))
               .select(["pr_id", "sha", "filename", "patch"]).collect())
        commits = defaultdict(list)              # pr_id -> [(sha, filename, patch)]
        for row in sub.iter_rows(named=True):
            commits[row["pr_id"]].append((row["sha"], row["filename"], row["patch"]))

        for pid in batch:
            pc = commits.get(pid, [])
            for r in by_pr.get(pid, []):
                sha, path = r["sha"], r["path"]
                try:
                    satd_line = int(str(r["line_span"]).split("-")[0])
                except (TypeError, ValueError):
                    satd_line = None

                # the SATD's enclosing-method heading, from its OWN introducing-commit hunk
                method = None
                for (s, f, p) in pc:
                    if s == sha and f == path:
                        for (a, b, h) in hunks(p):
                            if satd_line is not None and a <= satd_line <= b:
                                method = h or None
                                break
                        break
                if method:
                    heading_anchored += 1
                elif satd_line is not None:
                    window_anchored += 1
                else:
                    unanchored += 1

                resolved_at = r["resolved_at"]
                n_file = n_near = n_pre = 0
                for (s, f, p) in pc:
                    if f != path or s == sha:    # other commits touching the same file
                        continue
                    n_file += 1
                    hs = hunks(p)
                    hit = bool(method) and any(h == method for (_, _, h) in hs)
                    if not hit and satd_line is not None:       # heading-less fallback: window overlap
                        hit = any(not (b < satd_line - WINDOW or a > satd_line + WINDOW) for (a, b, _) in hs)
                    if hit:
                        n_near += 1
                        cd = dates.get(s)                       # revisit strictly BEFORE resolution
                        if cd and resolved_at and cd < resolved_at:
                            n_pre += 1
                if n_near:
                    with_revisit += 1
                upd = {"n_ai_file_edits": n_file, "n_ai_near_edits": n_near}
                if dates and resolved_at:                       # only meaningful once dates exist
                    upd["n_revisits_pre_res"] = n_pre
                store.update_satd_status(conn, r["id"], **upd)
                done += 1
        conn.commit(); print(f"  {done}/{len(insts)}", flush=True)

    print(f"\ndone. anchoring — method-heading: {heading_anchored}, line-window: {window_anchored}, "
          f"unanchored: {unanchored}")
    print(f"SATD with >=1 in-method revisit (edited but not necessarily resolved): {with_revisit}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--aidev-dir", default="data/aidev")
    ap.add_argument("--chunk", type=int, default=4000)
    run(**vars(ap.parse_args()))
