"""Stage 3a — Channel A: SATD in code comments, on ADDED lines only.

Reads archived patches, keeps '+' lines, extracts comment text, and matches the
TODO/FIXME/HACK/XXX + Potdar&Shihab/Maldonado phrase core (see satd_detect.py).
A bare TODO does NOT count — it must co-occur with a debt phrase. Only real source
files are scanned (satd_detect.is_source_file), so casual TODOs in CI YAML / config
are not counted. Each hit is classified design | bug | refactoring and written to
satd_instances (channel='code_comment'). A random sample is queued for precision
validation (~12% false positives expected per Brahmi).

Run:  python stage3_satd_scan.py [--sample 0.1]
"""
import argparse
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import config  # noqa: F401
import satd_detect as sd
from db import store

LINE_COMMENT = re.compile(r"(?://+|#+|--)\s?(.*)$")
BLOCK_COMMENT = re.compile(r"/\*+(.*?)\*+/|<!--(.*?)-->", re.DOTALL)
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _hunks(patch: str):
    """Yield (new_start_line, [raw diff lines of the hunk incl. @@ header])."""
    start, buf = None, None
    for line in patch.splitlines():
        m = HUNK_HEADER.match(line)
        if m:
            if buf is not None:
                yield start, buf
            start, buf = int(m.group(1)), [line]
        elif buf is not None:
            buf.append(line)
    if buf is not None:
        yield start, buf


def added_comments(patch: str):
    """Yield (line_no, raw_comment_text, hunk_text) for comments on added lines of a diff.
    hunk_text is the full @@ hunk containing the comment — the exact code block AI introduced."""
    for start, buf in _hunks(patch):
        cur = start
        hunk_text = "\n".join(buf)
        for line in buf[1:]:
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                content = line[1:]
                lc = LINE_COMMENT.search(content)
                if lc and lc.group(1).strip():
                    yield cur, lc.group(1), hunk_text
                for b in BLOCK_COMMENT.finditer(content):
                    frag = (b.group(1) or b.group(2) or "").strip()
                    if frag:
                        yield cur, frag, hunk_text
                cur += 1
            elif line.startswith("-"):
                continue  # removed line: don't advance the new-file counter
            else:
                cur += 1  # context line


def scan_file_change(conn, repo, sha, pr_number, path, patch_text, patch_ref=None):
    if not sd.is_source_file(path):
        return 0  # skip CI YAML / config / docs — not source-code comments
    # who introduced it — classify PR author + commit author/committer/co-authors as a
    # coding agent vs automation vs human (agents.classify_actor, single source of truth)
    cm = conn.execute("SELECT author_login, committer_login, co_authors, author_class "
                      "FROM commits WHERE sha=?", (sha,)).fetchone()
    pr = conn.execute("SELECT author, author_type FROM prs WHERE repo=? AND pr_number=?",
                      (repo, pr_number)).fetchone()
    ids = ([pr["author"], pr["author_type"]] if pr else []) + \
          ([cm["author_login"], cm["committer_login"], cm["co_authors"]] if cm else [])
    kind, aname = agents.classify_actor(*ids)
    committed = agents.committed_by(cm["author_login"] if cm else None,
                                    cm["committer_login"] if cm else None)
    aclass = (cm["author_class"] if cm and cm["author_class"] else (pr["author_type"] if pr else None))
    found = 0
    for line_no, raw, hunk in added_comments(patch_text):
        kw = sd.detect(raw)          # None for a bare TODO or no debt signal
        if not kw:
            continue
        norm = sd.normalize(raw)
        if not norm:
            continue
        inst_id = store.upsert_satd_instance(
            conn, repo=repo, introducing_sha=sha, path=path, comment_text_norm=norm,
            comment_text_raw=raw.strip()[:200],   # literal snippet for Stage-5 git pickaxe
            introducing_pr=pr_number, line_span=str(line_no), keyword=kw,
            satd_type=sd.classify_type(raw),      # design | bug | refactoring
            channel="code_comment", source_kind="added_line_comment",
            author_class=aclass,                  # who introduced it: ai | human (legacy)
            introducer_kind=kind, agent_name=aname, committed_by=committed,
            intro_patch_ref=patch_ref, intro_hunk=hunk[:1500],   # exact code block AI added
        )
        store.enqueue_validation(conn, "flagged_satd", "satd_instances", inst_id,
                                 {"repo": repo, "sha": sha, "path": path, "line": line_no,
                                  "keyword": kw, "text": raw})
        found += 1
    return found


def run(sample=0.1, seed=42):
    random.seed(seed)
    conn = store.connect()
    rows = conn.execute(
        "SELECT fc.sha, fc.path, fc.patch_ref, c.pr_id, p.repo, p.pr_number "
        "FROM file_changes fc JOIN commits c ON c.sha=fc.sha JOIN prs p ON p.id=c.pr_id "
        "WHERE fc.patch_ref IS NOT NULL"
    ).fetchall()
    total_hits = total_added_comments = 0
    for r in rows:
        patch_path = config.DATA_DIR / r["patch_ref"]
        if not patch_path.exists():
            continue
        patch = patch_path.read_text(errors="replace")
        if sd.is_source_file(r["path"]):
            total_added_comments += sum(1 for _ in added_comments(patch))
        total_hits += scan_file_change(conn, r["repo"], r["sha"], r["pr_number"], r["path"],
                                       patch, patch_ref=r["patch_ref"])
    conn.commit()

    # sanity vs Brahmi's published per-COMMIT rates (2.6% AI / 4.25% human commits)
    n_commits = conn.execute("SELECT COUNT(DISTINCT sha) n FROM commits").fetchone()["n"] or 1
    commits_with_satd = conn.execute(
        "SELECT COUNT(DISTINCT introducing_sha) n FROM satd_instances WHERE channel='code_comment'"
    ).fetchone()["n"]
    print(f"scanned {len(rows)} file-changes | source added-comments: {total_added_comments} | SATD hits: {total_hits}")
    print(f"commits with >=1 code-comment SATD: {commits_with_satd}/{n_commits} "
          f"= {100*commits_with_satd/n_commits:.2f}%  (baseline AI 2.6% / human 4.25%)")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=float, default=0.1, help="precision-validation sample fraction")
    run(**vars(ap.parse_args()))
