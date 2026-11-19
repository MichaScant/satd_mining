"""Stage 2 (AIDev) — populate commits (and optionally review_events) for AIDev PRs from the local
parquet tables, so pooled metrics that need commit/conversation context work for AIDev too — no
GitHub fetch. Run stage1_aidev first (PRs must be in `prs`).

  python stage2_aidev.py --aidev-dir data/aidev --exclude copilot --what commits
  python stage2_aidev.py --aidev-dir data/aidev --exclude copilot --what reviews
  python stage2_aidev.py --aidev-dir data/aidev --exclude copilot --what all

commits  -> from pr_commits.parquet (sha, pr_id, author, committer, message). Fixes the
            documentation-RATE denominator and makes satd_instances<->commits joins resolve.
            NOTE: AIDev has no commit-level dates -> phase defaults to first_attempt and
            survival/time-to-resolution stay Suzhen-only.
reviews  -> from pr_comments / pr_review_comments / pr_reviews -> review_events (conversational
            channel + elicited-vs-spontaneous for AIDev). COLMAP_REVIEW is self-diagnosing: if a
            column name doesn't match, it prints the file's columns and skips that table.
"""
import argparse
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
from db import store
from stage1_aidev import AIDEV_AGENT_MAP
from stage3_aidev import _repo_num

# review tables -> review_events columns. Edit if the diagnostics show different names.
COLMAP_REVIEW = {
    "pr_comments.parquet":        {"ts": "created_at", "actor": "user", "body": "body", "kind": "comment", "file_path": None},
    "pr_review_comments.parquet": {"ts": "created_at", "actor": "user", "body": "body", "kind": "inline", "file_path": "path"},
    "pr_reviews.parquet":         {"ts": "submitted_at", "actor": "user", "body": "body", "kind": "review", "file_path": None},
}


def _idmap(d, agents_filter, exclude):
    pr = pl.read_parquet(d / "pull_request.parquet", columns=["id", "html_url", "agent"])
    want = set(AIDEV_AGENT_MAP)
    if agents_filter:
        pick = {a.strip().lower() for a in agents_filter.split(",") if a.strip()}
        want = {l for l, n in AIDEV_AGENT_MAP.items() if n in pick}
    elif exclude:
        drop = {a.strip().lower() for a in exclude.split(",") if a.strip()}
        want = {l for l, n in AIDEV_AGENT_MAP.items() if n not in drop}
    m = {}
    for row in pr.iter_rows(named=True):
        lbl = str(row["agent"] or "").strip().lower()
        if lbl not in want:
            continue
        repo, num = _repo_num(row["html_url"])
        if repo:
            m[row["id"]] = (repo, num, AIDEV_AGENT_MAP[lbl])
    return m


def load_commits(conn, d, idmap, prslook, chunk):
    f = d / "pr_commits.parquet"
    cols = [c for c in ["sha", "pr_id", "author", "committer", "message"]
            if c in pl.scan_parquet(f).collect_schema().names()]
    ids = list(idmap); n = 0
    for i in range(0, len(ids), chunk):
        sub = (pl.scan_parquet(f).filter(pl.col("pr_id").is_in(ids[i:i + chunk]))
               .unique(subset=["sha"]).select(cols).collect())
        for r in sub.iter_rows(named=True):
            meta = idmap.get(r["pr_id"])
            if not meta:
                continue
            repo, num, name = meta
            prid = prslook.get((repo, num))
            if not prid:
                continue
            msg = r.get("message")
            store.upsert_commit(
                conn, sha=r["sha"], pr_id=prid,
                author_login=str(r.get("author") or "") or None,
                committer_login=str(r.get("committer") or "") or None,
                message=msg, author_class="ai", phase="first_attempt",
                agent_version=agents.extract_agent_version(msg, r.get("author")),
            )
            n += 1
        conn.commit(); print(f"  commits: {n}")
    print(f"done: {n} AIDev commits loaded")


def load_reviews(conn, d, idmap, prslook, chunk):
    n = 0
    for fname, cm in COLMAP_REVIEW.items():
        f = d / fname
        if not f.exists():
            print(f"  (skip {fname}: not found)"); continue
        have = pl.scan_parquet(f).collect_schema().names()
        need = [cm[k] for k in ("ts", "actor", "body") if cm.get(k)]
        miss = [c for c in need if c not in have]
        if miss or "pr_id" not in have:
            print(f"  (skip {fname}: missing {miss or ['pr_id']}; columns = {have})"); continue
        sel = ["pr_id"] + [cm[k] for k in ("ts", "actor", "body", "file_path") if cm.get(k) and cm[k] in have]
        ids = list(idmap)
        for i in range(0, len(ids), chunk):
            sub = pl.scan_parquet(f).filter(pl.col("pr_id").is_in(ids[i:i + chunk])).select(sel).collect()
            for r in sub.iter_rows(named=True):
                meta = idmap.get(r["pr_id"])
                if not meta:
                    continue
                repo, num, name = meta
                prid = prslook.get((repo, num))
                if not prid:
                    continue
                actor = r.get(cm["actor"])
                store.add_review_event(
                    conn, pr_id=prid, ts=r.get(cm["ts"]), actor=str(actor or "") or None,
                    actor_type=agents.classify_actor(actor)[0], kind=cm["kind"],
                    file_path=(r.get(cm["file_path"]) if cm.get("file_path") else None),
                    body=r.get(cm["body"]),
                )
                n += 1
            conn.commit(); print(f"  {fname}: {n} review_events so far")
    print(f"done: {n} AIDev review_events loaded")


def run(aidev_dir="data/aidev", agents_filter=None, exclude=None, what="all", chunk=40000):
    d = Path(aidev_dir)
    idmap = _idmap(d, agents_filter, exclude)
    print(f"AIDev PRs in scope: {len(idmap)}")
    conn = store.connect(); store.init_db(conn)
    prslook = {(r[0], r[1]): r[2] for r in conn.execute("SELECT repo, pr_number, id FROM prs")}
    if what in ("commits", "all"):
        load_commits(conn, d, idmap, prslook, chunk)
    if what in ("reviews", "all"):
        load_reviews(conn, d, idmap, prslook, chunk)
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--aidev-dir", default="data/aidev")
    ap.add_argument("--agents", dest="agents_filter", default=None)
    ap.add_argument("--exclude", default=None)
    ap.add_argument("--what", choices=["commits", "reviews", "all"], default="all")
    ap.add_argument("--chunk", type=int, default=40000)
    run(**vars(ap.parse_args()))
