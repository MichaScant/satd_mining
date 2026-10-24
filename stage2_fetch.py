"""Stage 2 — fetch code from GitHub for each selected PR.

Per PR: GET /repos/{repo}/pulls/{n}/commits, then per commit GET /repos/{repo}/commits/{sha}
for file-level patches. ~2-3 calls/PR. Resumable from fetch_log; every patch archived to
disk with its fetch date. Rate-limit aware (5k/hr per token).

Phase label per commit: before first review = first_attempt; after a changes_requested = revision;
commit author != PR author = human_applied.
Author class: PR author_type (Suzhen) + committer account + Co-Authored-By trailers; conflicts logged.

Run:  GITHUB_TOKEN=... python stage2_fetch.py [--limit N]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from db import store

CO_AUTHOR_RE = re.compile(r"Co-Authored-By:\s*([^<]+)<([^>]+)>", re.IGNORECASE)


def _headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if config.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return h


def _get(url, params=None):
    """GET with rate-limit handling. Returns (status_code, json_or_None)."""
    while True:
        r = requests.get(url, headers=_headers(), params=params, timeout=30)
        if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(5, reset - int(time.time()) + 2)
            print(f"   rate-limited; sleeping {wait}s")
            time.sleep(wait)
            continue
        if r.status_code in (404, 451):   # gone / DMCA
            return r.status_code, None
        if r.status_code >= 500:
            time.sleep(5)
            continue
        return r.status_code, (r.json() if r.content else None)


def _phase(commit_authored_at, pr_author, commit_author, first_review_ts, first_change_req_ts):
    if commit_author and pr_author and commit_author != pr_author:
        return "human_applied"
    if first_change_req_ts and commit_authored_at and commit_authored_at >= first_change_req_ts:
        return "revision"
    if first_review_ts and commit_authored_at and commit_authored_at >= first_review_ts:
        return "revision"
    return "first_attempt"


def _author_class(pr_author_type, committer_login, co_authors):
    """Combine Suzhen label + committer account + co-author trailers; flag conflicts."""
    signals = {pr_author_type}
    low = (committer_login or "").lower()
    if any(b in low for b in ("copilot", "devin", "cursor", "claude", "codex", "[bot]", "-bot")):
        signals.add("ai")
    elif committer_login:
        signals.add("human")
    for name, _ in co_authors:
        nl = name.lower()
        if any(b in nl for b in ("copilot", "devin", "cursor", "claude", "codex", "bot")):
            signals.add("ai")
    signals.discard(None)
    if signals == {"ai"}:
        return "ai", 0
    if signals == {"human"}:
        return "human", 0
    return "conflict", 1


def _review_timestamps(conn, pr_id):
    rows = conn.execute(
        "SELECT ts, kind FROM review_events WHERE pr_id=? AND ts IS NOT NULL ORDER BY ts", (pr_id,)
    ).fetchall()
    first_review = rows[0]["ts"] if rows else None
    first_cr = next((r["ts"] for r in rows if r["kind"] == "changes_requested"), None)
    return first_review, first_cr


def fetch_pr(conn, repo, pr_number, pr_id, pr_author, pr_author_type):
    first_review, first_cr = _review_timestamps(conn, pr_id)
    sc, commits = _get(f"{config.GITHUB_API}/repos/{repo}/pulls/{pr_number}/commits")
    if sc == 404 or commits is None:
        store.log_fetch(conn, repo, pr_number, "gone", f"pulls/commits -> {sc}")
        return
    for cm in commits:
        sha = cm["sha"]
        author_login = (cm.get("author") or {}).get("login")
        committer_login = (cm.get("committer") or {}).get("login")
        message = (cm.get("commit") or {}).get("message", "")
        co_authors = CO_AUTHOR_RE.findall(message)
        authored_at = (cm.get("commit") or {}).get("author", {}).get("date")

        sc2, detail = _get(f"{config.GITHUB_API}/repos/{repo}/commits/{sha}")
        if detail is None:
            store.log_fetch(conn, repo, pr_number, "gone", f"commit {sha[:8]} -> {sc2}")
            continue

        aclass, conflict = _author_class(pr_author_type, committer_login,
                                         [(n.strip(), e) for n, e in co_authors])
        store.upsert_commit(
            conn, sha, pr_id, author_login=author_login, committer_login=committer_login,
            co_authors=[n.strip() for n, _ in co_authors], authored_at=authored_at,
            phase=_phase(authored_at, pr_author, author_login, first_review, first_cr),
            author_class=aclass, author_conflict=conflict,
        )
        # archive + record file patches
        for f in detail.get("files", []):
            patch = f.get("patch")
            patch_ref = None
            if patch:
                if len(patch.encode()) > config.FETCH_MAX_PATCH_BYTES:
                    store.log_fetch(conn, repo, pr_number, "too_large", f"{sha[:8]}:{f['filename']}")
                else:
                    d = config.RAW_DIFFS_DIR / repo.replace("/", "__") / sha
                    d.mkdir(parents=True, exist_ok=True)
                    patch_ref = str((d / (f["filename"].replace("/", "__") + ".patch")).relative_to(config.DATA_DIR))
                    (config.DATA_DIR / patch_ref).write_text(patch)
            store.add_file_change(conn, sha, f["filename"], patch_ref,
                                  f.get("additions"), f.get("deletions"))
    store.log_fetch(conn, repo, pr_number, "ok")
    conn.commit()


def run(limit=None):
    if not config.GITHUB_TOKEN:
        print("WARNING: no GITHUB_TOKEN set — unauthenticated requests are capped at 60/hr.")
    conn = store.connect()
    prs = conn.execute("SELECT id, repo, pr_number, author, author_type FROM prs ORDER BY id").fetchall()
    todo = [p for p in prs if not store.already_fetched(conn, p["repo"], p["pr_number"])]
    if limit:
        todo = todo[:limit]
    print(f"{len(prs)} PRs total, {len(todo)} to fetch (rest resumed from fetch_log)")
    for i, p in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {p['repo']}#{p['pr_number']}")
        try:
            fetch_pr(conn, p["repo"], p["pr_number"], p["id"], p["author"], p["author_type"])
        except Exception as e:
            store.log_fetch(conn, p["repo"], p["pr_number"], "rate_limited", str(e)[:200])
            conn.commit()
    conn.close()
    print("done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    run(**vars(ap.parse_args()))
