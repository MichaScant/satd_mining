"""Stage 5 — resolution tracking (within-PR + cross-PR). The biggest piece.

For each detected SATD instance:
  1. Clone the repo once (--filter=blob:none), reused across its instances.
  2. Anchor the comment at the introducing PR's merge commit (confirm it exists post-merge;
     if it was already gone by merge but present at the introducing commit -> self_resolved_premerge).
  3. Find every later touch with git pickaxe (-S on the literal snippet) since the merge date —
     each commit where the comment's presence changes is an event (modified / removed / file_deleted).
  4. Also scan within-PR commits (revision phase) for early removal.
  5. Map each touching commit to its PR (GET /commits/{sha}/pulls).
  6. Classify the resolver: that PR's author_type (from the store) or commit trailers -> ai|human.
  7. Final status: resolved | comment_deleted_only | file_deleted | self_resolved_premerge |
     alive (right-censored; record censor date) | untrackable.

Reworded-comment fallback: if pickaxe finds nothing but the anchor line region changed, fall
back to `git log -L` on that region and log how often it fires (pickaxe misses rewordings).
Tactics follow SATDBailiff (arXiv:2107.00073), Maldonado et al., Zampetti et al.

Run:  GITHUB_TOKEN=... python stage5_track.py [--limit N]
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import config
from db import store
from stage2_fetch import _get   # reuse rate-limit-aware GitHub GET

_stats = {"reworded_fallback_fires": 0, "untrackable": 0, "clone_fail": 0}


def git(clone, *args) -> str:
    r = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True)
    return r.stdout


def ensure_clone(repo, repo_url=None):
    dest = config.CLONES_DIR / repo.replace("/", "__")
    if (dest / ".git").exists():
        return dest
    url = repo_url or f"https://github.com/{repo}.git"
    subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", url, str(dest)],
                   capture_output=True, text=True)
    if (dest / ".git").exists():
        git(dest, "checkout", "-q", git(dest, "symbolic-ref", "--short", "HEAD").strip() or "HEAD")
        return dest
    _stats["clone_fail"] += 1
    return None


_merge_cache = {}
_pr_cache = {}


def merge_info(repo, pr_number):
    """merge_commit_sha + merged_at from GitHub (not in SWE-Review-Chat). Memoized per PR."""
    key = (repo, pr_number)
    if key not in _merge_cache:
        _, pr = _get(f"{config.GITHUB_API}/repos/{repo}/pulls/{pr_number}")
        _merge_cache[key] = (pr.get("merge_commit_sha"), pr.get("merged_at")) if pr else (None, None)
    return _merge_cache[key]


def present_in(clone, sha, path, snippet) -> bool:
    return snippet.lower() in git(clone, "show", f"{sha}:{path}").lower()


def pickaxe(clone, path, snippet, since=None):
    """Commits (sha, iso-date) where the count of `snippet` changed, newest-first."""
    args = ["log", "--follow", "--format=%H|%cI", f"-S{snippet}"]
    if since:
        args.append(f"--since={since}")
    args += ["--", path]
    return [ln.split("|", 1) for ln in git(clone, *args).splitlines() if "|" in ln]


def only_comment_changed(clone, sha, path) -> bool:
    """True if this commit's change to `path` touched only comment/blank lines (no real code)."""
    diff = git(clone, "show", "--format=", "-U0", sha, "--", path)
    for ln in diff.splitlines():
        if ln[:1] in "+-" and ln[:3] not in ("+++", "---"):
            body = ln[1:].strip()
            if body and not (body.startswith(("//", "#", "*", "/*", "<!--")) or body.endswith("*/")):
                return False  # a non-comment code line changed
    return True


def resolver_class(conn, repo, pr_number, sha, clone):
    """ai = a genuine coding agent resolved/touched it; else human. Automation is NOT ai."""
    if pr_number:
        row = conn.execute("SELECT author_type FROM prs WHERE repo=? AND pr_number=?",
                           (repo, pr_number)).fetchone()
        if row and row["author_type"] in ("ai", "human"):
            return row["author_type"]
    meta = git(clone, "show", "-s", "--format=%an|%cn|%b", sha)
    kind, _ = agents.classify_actor(meta)
    return "ai" if kind == "coding_agent" else "human"


def _fix_block(clone, sha, path, snippet):
    """The diff hunk where `sha` removed/changed the SATD in `path` — the exact fix code block."""
    diff = git(clone, "show", "--format=", "-U3", sha, "--", path)
    hunks, cur = [], []
    for ln in diff.splitlines():
        if ln.startswith("@@"):
            if cur:
                hunks.append(cur)
            cur = [ln]
        elif cur:
            cur.append(ln)
    if cur:
        hunks.append(cur)
    snip = snippet.lower()[:60]
    for h in hunks:                       # prefer the hunk that removed the SATD line
        if any(l[:1] == "-" and snip in l.lower() for l in h):
            return "\n".join(h)[:1500]
    return ("\n".join(hunks[0])[:1500]) if hunks else None


def map_to_pr(repo, sha):
    key = (repo, sha)
    if key not in _pr_cache:
        _, pulls = _get(f"{config.GITHUB_API}/repos/{repo}/commits/{sha}/pulls")
        _pr_cache[key] = pulls[0].get("number") if pulls else None
    return _pr_cache[key]


def within_pr_touches(conn, inst, snippet) -> bool:
    """Record within-PR events from the ARCHIVED patches (robust to squash-merge, which drops
    individual commit SHAs from the clone). Returns True if the comment was removed before merge."""
    iid, repo, path = inst["id"], inst["repo"], inst["path"]
    intro_sha = inst["introducing_sha"]
    pr_author = inst["pr_author_type"] if "pr_author_type" in inst.keys() else None
    rows = conn.execute(
        "SELECT c.sha, c.authored_at, fc.patch_ref FROM commits c "
        "JOIN prs p ON p.id = c.pr_id "
        "JOIN file_changes fc ON fc.sha = c.sha AND fc.path = ? "
        "WHERE p.repo = ? AND p.pr_number = ? ORDER BY c.authored_at",
        (path, repo, inst["introducing_pr"]),
    ).fetchall()
    snip = snippet.lower()
    removed = False
    seen_intro = intro_sha is None
    for r in rows:
        if r["sha"] == intro_sha:
            seen_intro = True
            continue
        if not seen_intro or not r["patch_ref"]:
            continue  # only commits AFTER the introducing commit
        pf = config.DATA_DIR / r["patch_ref"]
        if not pf.exists():
            continue
        lines = pf.read_text(errors="replace").splitlines()
        rem = any(l.startswith("-") and snip in l.lower() for l in lines)
        add = any(l.startswith("+") and snip in l.lower() for l in lines)
        if rem and not add:
            kind = "removed"
            removed = True
        elif rem and add:
            kind = "modified"
        else:
            continue  # touched the file but not the comment line
        store.add_satd_event(conn, iid, r["sha"], kind, event_pr=inst["introducing_pr"],
                             ts=r["authored_at"], scope="within_pr", actor_class=pr_author)
    return removed


def _days(a, b):
    try:
        pa = datetime.fromisoformat(a.replace("Z", "+00:00"))
        pb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return round((pb - pa).total_seconds() / 86400, 1)
    except Exception:
        return None


def _finalize_metrics(conn, iid, merged_at, end_date):
    ev = conn.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT event_pr) p FROM satd_events WHERE instance_id=?",
        (iid,)).fetchone()
    # how often a coding AGENT came back to the debt before resolution (AI revisits)
    ai = conn.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT event_pr) p FROM satd_events "
        "WHERE instance_id=? AND actor_class='ai'", (iid,)).fetchone()
    surv = _days(merged_at, end_date) if (merged_at and end_date) else None
    store.update_satd_status(conn, iid, survival_days=surv, n_events=ev["n"], n_distinct_prs=ev["p"],
                             n_ai_revisits=ai["n"], n_ai_revisit_prs=ai["p"])


def _record_ai_involvement(conn, iid, repo, pr_number):
    """If a human resolved it, how much AI was involved in the resolving PR (if it's in our store)."""
    if not pr_number:
        return
    row = conn.execute("SELECT id FROM prs WHERE repo=? AND pr_number=?", (repo, pr_number)).fetchone()
    if not row:
        return  # resolving PR is out-of-dataset -> leave null (would need a fetch)
    pid = row["id"]
    nc = conn.execute("SELECT COUNT(*) n FROM commits WHERE pr_id=? AND author_class='ai'", (pid,)).fetchone()["n"]
    nd = conn.execute("SELECT COUNT(*) n FROM review_events WHERE pr_id=? AND actor_type='ai'", (pid,)).fetchone()["n"]
    store.update_satd_status(conn, iid, resolver_ai_commits=nc, resolver_ai_discussion=nd)


def track_instance(conn, inst, clone):
    iid = inst["id"]
    repo, path = inst["repo"], inst["path"]
    snippet = (inst["comment_text_raw"] or inst["comment_text_norm"]).strip()
    msha, merged_at = merge_info(repo, inst["introducing_pr"]) if inst["introducing_pr"] else (None, None)

    # --- within-PR touches (archived patches; robust to squash-merge) ---
    removed_in_pr = within_pr_touches(conn, inst, snippet)

    # --- anchor on the merge commit ---
    if not msha or not present_in(clone, msha, path, snippet):
        if removed_in_pr or (inst["introducing_sha"] and present_in(clone, inst["introducing_sha"], path, snippet)):
            store.update_satd_status(conn, iid, status="self_resolved_premerge")
            _finalize_metrics(conn, iid, merged_at, merged_at)   # fixed before merge -> ~0 survival
        else:
            store.update_satd_status(conn, iid, status="untrackable")
            _stats["untrackable"] += 1
        conn.commit()
        return
    store.update_satd_status(conn, iid, anchor_merge_sha=msha, anchor_path=path)

    # --- cross-PR touches via pickaxe ---
    touches = pickaxe(clone, path, snippet, since=merged_at)
    resolved, resolved_at = False, None
    for sha, ts in touches:
        if sha in (inst["introducing_sha"], msha):
            continue
        file_gone = not git(clone, "cat-file", "-t", f"{sha}:{path}").strip()
        gone = file_gone or not present_in(clone, sha, path, snippet)
        kind = "file_deleted" if file_gone else ("removed" if gone else "modified")
        pr = map_to_pr(repo, sha)
        actor = resolver_class(conn, repo, pr, sha, clone)
        store.add_satd_event(conn, iid, sha, kind, event_pr=pr, ts=ts, scope="cross_pr", actor_class=actor)
        if not resolved and kind in ("removed", "file_deleted"):
            # resolved (real code change) vs comment_deleted_only (silent carry) — Zampetti distinction
            if kind == "removed" and only_comment_changed(clone, sha, path):
                final = "comment_deleted_only"
            else:
                final = "file_deleted" if kind == "file_deleted" else "resolved"
            store.update_satd_status(conn, iid, status=final, resolved_sha=sha, resolved_pr=pr,
                                     resolved_at=ts, resolver_class=actor, resolved_path=path,
                                     fix_hunk=_fix_block(clone, sha, path, snippet))
            store.enqueue_validation(conn, "tracker_outcome", "satd_instances", iid,
                                     {"repo": repo, "path": path, "status": final,
                                      "resolved_sha": sha, "resolver": actor, "snippet": snippet})
            if actor == "human":
                _record_ai_involvement(conn, iid, repo, pr)
            resolved, resolved_at = True, ts

    # --- reworded fallback: comment gone from HEAD but pickaxe attributed no removal ---
    if not resolved and inst["anchor_line"]:
        head_has = (git(clone, "cat-file", "-t", f"HEAD:{path}").strip()
                    and present_in(clone, "HEAD", path, snippet))
        if not head_has:
            region = git(clone, "log", "--format=%H|%cI", f"-L{inst['anchor_line']},{inst['anchor_line']}:{path}")
            first = next((l for l in region.splitlines() if "|" in l), None)
            if first:
                _stats["reworded_fallback_fires"] += 1
                rsha, rts = first.split("|", 1)
                pr = map_to_pr(repo, rsha)
                actor = resolver_class(conn, repo, pr, rsha, clone)
                store.add_satd_event(conn, iid, rsha, "reworded", event_pr=pr, ts=rts,
                                     scope="cross_pr", actor_class=actor)
                store.update_satd_status(conn, iid, status="resolved", resolved_sha=rsha,
                                         resolved_pr=pr, resolved_at=rts, resolver_class=actor,
                                         resolved_path=path, fix_hunk=_fix_block(clone, rsha, path, snippet))
                if actor == "human":
                    _record_ai_involvement(conn, iid, repo, pr)
                resolved, resolved_at = True, rts

    if resolved:
        _finalize_metrics(conn, iid, merged_at, resolved_at)
    else:
        head_date = git(clone, "log", "-1", "--format=%cI").strip()
        store.update_satd_status(conn, iid, status="alive", censor_date=head_date)
        _finalize_metrics(conn, iid, merged_at, head_date)
    conn.commit()


def run(limit=None):
    conn = store.connect()
    rows = conn.execute(
        "SELECT i.*, p.repo_url, p.author_type AS pr_author_type FROM satd_instances i "
        "LEFT JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE i.status IS NULL AND (i.channel IS NULL OR i.channel='code_comment') "
        "AND i.introducing_sha IS NOT NULL ORDER BY i.repo"
    ).fetchall()
    if limit:
        rows = rows[:limit]
    print(f"{len(rows)} instances to track")
    clones = {}
    for i, inst in enumerate(rows, 1):
        repo = inst["repo"]
        if repo not in clones:
            clones[repo] = ensure_clone(repo, inst["repo_url"] if "repo_url" in inst.keys() else None)
        clone = clones[repo]
        if clone is None:
            store.update_satd_status(conn, inst["id"], status="untrackable")
            conn.commit()
            continue
        print(f"[{i}/{len(rows)}] {repo}:{inst['path']} #{inst['introducing_pr']}")
        try:
            track_instance(conn, inst, clone)
        except Exception as e:
            print(f"   error: {e}")
    print(f"done. stats: {_stats}")
    conn.close()


def _has_col(conn, table, col):
    return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    run(**vars(ap.parse_args()))
