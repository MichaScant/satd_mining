"""Stage 5 (AIDev) — resolution tracking WITHOUT the GitHub API. AIDev gives us merged_at + state
locally (no merge_commit_sha, and the live API is unreliable/unauthed), so we anchor on the cloned
default branch instead of a PR merge commit:

  - PR not merged (merged_at IS NULL)      -> status 'pr_unmerged' / 'pr_open'. The SATD never
                                              entered the mainline codebase; excluded from survival.
  - merged, snippet present at HEAD         -> 'alive' (right-censored at HEAD date).
  - merged, snippet gone from HEAD          -> pickaxe (-S) since merged_at finds the removing commit
                                              -> 'resolved' / 'comment_deleted_only' / 'file_deleted'.
                                              resolver classified from the commit's git trailers
                                              (no API: event_pr left NULL).
  - merged, gone from HEAD but no removal on main -> 'untrackable' (content never reached default
                                              branch, e.g. side-branch-only; logged).

Reuses the Suzhen tracker's clone/pickaxe/classify helpers verbatim, so outcomes are comparable.
Run (no token needed — public clones are anonymous):
  python stage5_aidev.py [--limit N]
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import store
import stage5_track as s5   # ensure_clone, git, pickaxe, present_in, only_comment_changed,
                            # resolver_class, _fix_block, _finalize_metrics, _stats

_astats = {"pr_unmerged": 0, "pr_open": 0, "alive": 0, "resolved": 0, "comment_deleted_only": 0,
           "file_deleted": 0, "self_resolved_premerge": 0, "untrackable": 0}


# --- reflow-robust matching: squash/Prettier reformat the SAME comment with different whitespace,
#     so exact-byte pickaxe/`git show` miss it. Normalize whitespace+markers and match on that. ---
_WS = re.compile(r"\s+")
_LEAD = re.compile(r"^\s*(//+|/\*+|\*+|#+|<!--|--|;+)\s*")
_TAIL = re.compile(r"\s*(\*/|-->)\s*$")


def _norm(s):
    return _WS.sub(" ", s or "").strip().lower()


def _probe(text):
    """Whitespace/marker-insensitive core of the comment (>=15 chars to be usable)."""
    s = _TAIL.sub("", _LEAD.sub("", (text or "").strip())).strip()
    return _norm(s)


def _file_at(clone, ref, path):
    return bool(s5.git(clone, "cat-file", "-t", f"{ref}:{path}").strip())


def _is_ancestor(clone, a, b):
    """True if commit `a` is in the history of `b` (i.e. `a` really landed on that branch)."""
    import subprocess
    return subprocess.run(["git", "-C", str(clone), "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


def _norm_present(clone, ref, path, probe):
    return bool(probe) and len(probe) >= 15 and probe in _norm(s5.git(clone, "show", f"{ref}:{path}"))


def _find_removal_norm(clone, path, probe, since, cap=120):
    """Oldest-first, bounded scan of commits touching `path` since `since`; the first commit where
    the whitespace-normalized comment goes present->absent is the removal. Best-effort fallback for
    reformatted comments that exact pickaxe misses."""
    if not probe or len(probe) < 15:
        return None, None
    log = s5.git(clone, "log", "--follow", "--format=%H|%cI", f"--since={since}", "--", path)
    commits = [ln.split("|", 1) for ln in log.splitlines() if "|" in ln]
    prev = True
    for sha, ts in list(reversed(commits))[:cap]:   # oldest-first
        here = probe in _norm(s5.git(clone, "show", f"{sha}:{path}"))
        if prev and not here:
            return sha, ts
        prev = here
    return None, None


def track_aidev(conn, inst, clone):
    iid, repo, path = inst["id"], inst["repo"], inst["path"]
    snippet = (inst["comment_text_raw"] or inst["comment_text_norm"] or "").strip()
    probe = _probe(inst["comment_text_norm"] or snippet)
    merged_at = inst["merged_at"] if "merged_at" in inst.keys() else None
    state = (inst["state"] if "state" in inst.keys() else "") or ""

    # 1. unmerged -> the debt never landed in the codebase
    if not merged_at:
        st = "pr_open" if state.lower() == "open" else "pr_unmerged"
        store.update_satd_status(conn, iid, status=st)
        _astats[st] += 1
        conn.commit()
        return st

    def _alive():
        head_sha = s5.git(clone, "rev-parse", "HEAD").strip()
        head_date = s5.git(clone, "log", "-1", "--format=%cI").strip()
        store.update_satd_status(conn, iid, status="alive", anchor_merge_sha=head_sha,
                                 anchor_path=path, censor_date=head_date)
        s5._finalize_metrics(conn, iid, merged_at, head_date)
        _astats["alive"] += 1
        conn.commit()
        return "alive"

    def _resolve(sha, ts, file_gone):
        kind = "file_deleted" if file_gone else "removed"
        actor = s5.resolver_class(conn, repo, None, sha, clone)   # None pr -> git-trailer classify
        if kind == "removed" and s5.only_comment_changed(clone, sha, path):
            final = "comment_deleted_only"
        else:
            final = "file_deleted" if file_gone else "resolved"
        s5.store.add_satd_event(conn, iid, sha, kind, event_pr=None, ts=ts,
                                scope="cross_pr", actor_class=actor)
        store.update_satd_status(conn, iid, status=final, resolved_sha=sha, resolved_at=ts,
                                 resolver_class=actor, resolved_path=path,
                                 fix_hunk=s5._fix_block(clone, sha, path, snippet))
        s5._finalize_metrics(conn, iid, merged_at, ts)
        _astats[final] += 1
        conn.commit()
        return final

    # 2. still present at HEAD (exact OR whitespace-normalized / reflowed) -> alive
    if s5.present_in(clone, "HEAD", path, snippet) or _norm_present(clone, "HEAD", path, probe):
        return _alive()

    # 3. gone from HEAD -> exact pickaxe finds the removing commit (oldest-first)
    for sha, ts in reversed(s5.pickaxe(clone, path, snippet, since=merged_at)):
        if sha == inst["introducing_sha"]:
            continue
        file_gone = not _file_at(clone, sha, path)
        if file_gone or not s5.present_in(clone, sha, path, snippet):
            return _resolve(sha, ts, file_gone)

    # 4. reflow-robust removal scan (reformatted comment that exact -S missed)
    sha, ts = _find_removal_norm(clone, path, probe, merged_at)
    if sha:
        return _resolve(sha, ts, not _file_at(clone, sha, path))

    # 5. path itself gone from HEAD -> attribute to the deletion commit
    if not _file_at(clone, "HEAD", path):
        d = s5.git(clone, "log", "--diff-filter=D", "--format=%H|%cI", "--follow", "--", path)
        first = next((l for l in d.splitlines() if "|" in l), None)
        if first:
            s, t = first.split("|", 1)
            return _resolve(s, t, True)

    # 6. comment is at the introducing commit but never on the default branch, and that commit
    #    isn't in mainline history (squash-merge) -> it was removed before/at merge.
    isha = inst["introducing_sha"]
    at_intro = isha and (s5.present_in(clone, isha, path, snippet) or _norm_present(clone, isha, path, probe))
    if at_intro and not _is_ancestor(clone, isha, "HEAD"):
        store.update_satd_status(conn, iid, status="self_resolved_premerge")
        s5._finalize_metrics(conn, iid, merged_at, merged_at)   # fixed before merge -> ~0 survival
        _astats["self_resolved_premerge"] += 1
        conn.commit()
        return "self_resolved_premerge"

    # 7. merged, comment not on the default branch, and we couldn't pin how/when -> untrackable
    store.update_satd_status(conn, iid, status="untrackable")
    _astats["untrackable"] += 1
    conn.commit()
    return "untrackable"


def run(limit=None):
    conn = store.connect()
    store.init_db(conn)
    rows = conn.execute(
        "SELECT i.*, p.repo_url, p.author_type AS pr_author_type, p.merged_at, p.state "
        "FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.status IS NULL "
        "AND (i.channel IS NULL OR i.channel='code_comment') AND i.introducing_sha IS NOT NULL "
        "ORDER BY i.repo"
    ).fetchall()
    if limit:
        rows = rows[:limit]
    merged = sum(1 for r in rows if r["merged_at"])
    print(f"{len(rows)} AIDev instances to track  (merged={merged}, unmerged={len(rows) - merged})")
    clones = {}
    for i, inst in enumerate(rows, 1):
        repo = inst["repo"]
        if repo not in clones:
            clones[repo] = s5.ensure_clone(repo, inst["repo_url"] if "repo_url" in inst.keys() else None)
        clone = clones[repo]
        if clone is None:
            store.update_satd_status(conn, inst["id"], status="untrackable")
            _astats["untrackable"] += 1
            conn.commit()
            continue
        try:
            st = track_aidev(conn, inst, clone)
        except Exception as e:
            st = f"error: {e}"
        print(f"[{i}/{len(rows)}] {repo}:{inst['path']} #{inst['introducing_pr']} -> {st}")
    print(f"\ndone. outcomes: {_astats}")
    print(f"clone/helper stats: {s5._stats}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    run(**vars(ap.parse_args()))
