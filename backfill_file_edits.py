"""Backfill n_ai_file_edits / n_ai_near_edits for agent code-SATD instances that were tracked
BEFORE these metrics existed. Stage 5 is resumable (WHERE status IS NULL), so already-tracked
instances were skipped and never got these two counts. This recomputes them from the ARCHIVED
within-PR patches (no clone needed) — same logic as stage5_track.within_pr_touches.

  n_ai_file_edits = AI commits (after introduction) that touched the SATD's file (any line)
  n_ai_near_edits = of those, commits whose diff includes the SATD comment (change within the
                    diff-context window of the SATD location)

Run on the SERVER (needs data/ patches). Idempotent: only fills rows where n_ai_file_edits IS NULL.
  python3 backfill_file_edits.py
"""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config
from db import store


def count_edits(conn, inst):
    snippet = (inst["comment_text_raw"] or inst["comment_text_norm"] or "").strip().lower()
    if not snippet or not inst["introducing_pr"]:
        return 0, 0
    intro_sha = inst["introducing_sha"]
    pr_author = inst["pr_author_type"]
    rows = conn.execute(
        "SELECT c.sha, c.author_class, fc.patch_ref FROM commits c "
        "JOIN prs p ON p.id = c.pr_id "
        "JOIN file_changes fc ON fc.sha = c.sha AND fc.path = ? "
        "WHERE p.repo = ? AND p.pr_number = ? ORDER BY c.authored_at",
        (inst["path"], inst["repo"], inst["introducing_pr"]),
    ).fetchall()
    n_file = n_near = 0
    seen_intro = intro_sha is None
    for r in rows:
        if r["sha"] == intro_sha:
            seen_intro = True
            continue
        if not seen_intro or not r["patch_ref"]:
            continue
        pf = config.DATA_DIR / r["patch_ref"]
        if not pf.exists():
            continue
        is_ai = ((r["author_class"] or pr_author) or "") != "human"
        if not is_ai:
            continue
        text = pf.read_text(errors="replace").lower()
        n_file += 1
        if snippet in text:
            n_near += 1
    return n_file, n_near


def run():
    conn = store.connect()
    store.init_db(conn)
    conn.row_factory = sqlite3.Row
    todo = conn.execute(
        "SELECT i.id id, i.repo repo, i.introducing_pr introducing_pr, i.introducing_sha introducing_sha, "
        "i.path path, i.comment_text_raw comment_text_raw, i.comment_text_norm comment_text_norm, "
        "p.author_type pr_author_type "
        "FROM satd_instances i LEFT JOIN prs p ON p.repo = i.repo AND p.pr_number = i.introducing_pr "
        "WHERE i.channel='code_comment' AND i.introducer_kind='coding_agent' "
        "AND i.n_ai_file_edits IS NULL AND i.introducing_pr IS NOT NULL"
    ).fetchall()
    print(f"instances to backfill: {len(todo)}")
    done = withfile = withnear = 0
    for inst in todo:
        nf, nn = count_edits(conn, inst)
        store.update_satd_status(conn, inst["id"], n_ai_file_edits=nf, n_ai_near_edits=nn)
        done += 1
        withfile += 1 if nf else 0
        withnear += 1 if nn else 0
        if done % 500 == 0:
            conn.commit(); print(f"  {done}/{len(todo)}")
    conn.commit()
    print(f"done: {done} backfilled  (with >=1 file edit: {withfile}, with >=1 near edit: {withnear})")
    conn.close()


if __name__ == "__main__":
    run()
