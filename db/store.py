"""SQLite store: init from schema + idempotent upserts keyed on natural ids.

Every writer uses INSERT ... ON CONFLICT so re-running a stage never duplicates rows.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def connect(db_path=None):
    # timeout + busy_timeout: wait (up to 60s) on a transient lock instead of erroring
    # out immediately — e.g. a run interrupted with Ctrl+C whose lock hasn't cleared yet,
    # or the other shared-filesystem box briefly writing.
    conn = sqlite3.connect(str(db_path or config.DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn=None):
    """Create all tables (idempotent). Returns an OPEN connection for the caller to use/close."""
    conn = conn or connect()
    conn.executescript(config.SCHEMA_PATH.read_text())
    # idempotent migrations for columns added after a DB was first created
    for table, col, decl in (
        ("satd_instances", "satd_type", "TEXT"),
        ("satd_instances", "channel", "TEXT"),
        ("satd_instances", "source_kind", "TEXT"),
        ("satd_instances", "utterance_id", "INTEGER"),
        # coding-agent attribution (vs automation/human) + who merged it
        ("satd_instances", "introducer_kind", "TEXT"),      # coding_agent|automation_bot|human
        ("satd_instances", "agent_name", "TEXT"),           # copilot|devin|claude|...
        ("satd_instances", "committed_by", "TEXT"),         # agent|github_merge|human
        # AI revisiting the debt before it's resolved
        ("satd_instances", "n_ai_revisits", "INTEGER"),
        ("satd_instances", "n_ai_revisit_prs", "INTEGER"),
        # exact code blocks: where AI introduced it, and where it was fixed
        ("satd_instances", "intro_patch_ref", "TEXT"),      # archived introducing patch on disk
        ("satd_instances", "intro_hunk", "TEXT"),           # diff hunk that added the SATD
        ("satd_instances", "resolved_path", "TEXT"),
        ("satd_instances", "fix_hunk", "TEXT"),             # diff hunk that removed/changed it
        ("satd_instances", "survival_days", "REAL"),
        ("satd_instances", "n_events", "INTEGER"),
        ("satd_instances", "n_distinct_prs", "INTEGER"),
        ("satd_instances", "first_pointer_class", "TEXT"),
        ("satd_instances", "first_pointer_level", "TEXT"),
        ("satd_instances", "first_pointer_ts", "TEXT"),
        ("satd_instances", "resolver_ai_commits", "INTEGER"),
        ("satd_instances", "resolver_ai_discussion", "INTEGER"),
        ("reviewer_flags", "actor_type", "TEXT"),
        ("reviewer_flags", "file_path", "TEXT"),
        ("reviewer_flags", "ts", "TEXT"),
        ("reviewer_flags", "level", "TEXT"),
    ):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()
    return conn


def _uid(*parts) -> str:
    return hashlib.sha1("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()


# ---------------------------------------------------------------- Stage 1
def upsert_pr(conn, repo, pr_number, **cols) -> int:
    """Insert or update a PR; returns prs.id."""
    cols = {k: v for k, v in cols.items() if v is not None}
    fields = ["repo", "pr_number"] + list(cols)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in cols) or "repo=excluded.repo"
    conn.execute(
        f"INSERT INTO prs ({', '.join(fields)}) VALUES ({placeholders}) "
        f"ON CONFLICT(repo, pr_number) DO UPDATE SET {updates}",
        [repo, pr_number] + list(cols.values()),
    )
    row = conn.execute(
        "SELECT id FROM prs WHERE repo=? AND pr_number=?", (repo, pr_number)
    ).fetchone()
    return row["id"]


def add_review_event(conn, pr_id, ts=None, actor=None, actor_type=None,
                     kind=None, file_path=None, body=None):
    uid = _uid(pr_id, ts, actor, kind, file_path, body)
    conn.execute(
        "INSERT OR IGNORE INTO review_events "
        "(pr_id, ts, actor, actor_type, kind, file_path, body, event_uid) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (pr_id, ts, actor, actor_type, kind, file_path, body, uid),
    )


# ---------------------------------------------------------------- Stage 2
def upsert_commit(conn, sha, pr_id, **cols):
    cols = {k: (json.dumps(v) if k == "co_authors" and v is not None else v)
            for k, v in cols.items() if v is not None}
    fields = ["sha", "pr_id"] + list(cols)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in cols) or "pr_id=excluded.pr_id"
    conn.execute(
        f"INSERT INTO commits ({', '.join(fields)}) VALUES ({placeholders}) "
        f"ON CONFLICT(sha) DO UPDATE SET {updates}",
        [sha, pr_id] + list(cols.values()),
    )


def add_file_change(conn, sha, path, patch_ref=None, additions=None, deletions=None):
    conn.execute(
        "INSERT INTO file_changes (sha, path, patch_ref, additions, deletions) "
        "VALUES (?,?,?,?,?) ON CONFLICT(sha, path) DO UPDATE SET "
        "patch_ref=excluded.patch_ref, additions=excluded.additions, deletions=excluded.deletions",
        (sha, path, patch_ref, additions, deletions),
    )


def log_fetch(conn, repo, pr_number, status, detail=None):
    conn.execute(
        "INSERT INTO fetch_log (repo, pr_number, fetched_at, status, detail) "
        "VALUES (?,?,?,?,?) ON CONFLICT(repo, pr_number) DO UPDATE SET "
        "fetched_at=excluded.fetched_at, status=excluded.status, detail=excluded.detail",
        (repo, pr_number, now_iso(), status, detail),
    )


def already_fetched(conn, repo, pr_number) -> bool:
    row = conn.execute(
        "SELECT status FROM fetch_log WHERE repo=? AND pr_number=?", (repo, pr_number)
    ).fetchone()
    return bool(row and row["status"] == "ok")


# ---------------------------------------------------------------- Stage 3a
def upsert_satd_instance(conn, repo, introducing_sha, path, comment_text_norm, **cols) -> int:
    cols = {k: v for k, v in cols.items() if v is not None}
    fields = ["repo", "introducing_sha", "path", "comment_text_norm"] + list(cols)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in cols) or "repo=excluded.repo"
    conn.execute(
        f"INSERT INTO satd_instances ({', '.join(fields)}) VALUES ({placeholders}) "
        f"ON CONFLICT(repo, introducing_sha, path, comment_text_norm) DO UPDATE SET {updates}",
        [repo, introducing_sha, path, comment_text_norm] + list(cols.values()),
    )
    row = conn.execute(
        "SELECT id FROM satd_instances WHERE repo=? AND introducing_sha=? AND path=? AND comment_text_norm=?",
        (repo, introducing_sha, path, comment_text_norm),
    ).fetchone()
    return row["id"]


def add_satd_event(conn, instance_id, event_sha, kind, event_pr=None, ts=None,
                   scope=None, actor_class=None):
    conn.execute(
        "INSERT OR IGNORE INTO satd_events "
        "(instance_id, event_sha, event_pr, ts, kind, scope, actor_class) VALUES (?,?,?,?,?,?,?)",
        (instance_id, event_sha, event_pr, ts, kind, scope, actor_class),
    )


def update_satd_status(conn, instance_id, **cols):
    """Set status / resolver / censor fields on an instance (Stage 5)."""
    cols = {k: v for k, v in cols.items() if v is not None}
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE satd_instances SET {sets} WHERE id=?",
                 list(cols.values()) + [instance_id])


def add_reviewer_flag(conn, pr_id, event_id, category, actor_type=None, file_path=None,
                      ts=None, level=None, linked_fix_sha=None):
    conn.execute(
        "INSERT OR IGNORE INTO reviewer_flags "
        "(pr_id, event_id, category, actor_type, file_path, ts, level, linked_fix_sha) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (pr_id, event_id, category, actor_type, file_path, ts, level, linked_fix_sha),
    )


# ---------------------------------------------------------------- validation
def enqueue_validation(conn, queue, ref_table, ref_id, payload):
    conn.execute(
        "INSERT OR IGNORE INTO validation_queue (queue, ref_table, ref_id, payload, created_at) "
        "VALUES (?,?,?,?,?)",
        (queue, ref_table, ref_id, json.dumps(payload), now_iso()),
    )


if __name__ == "__main__":
    init_db()
    print(f"initialized schema at {config.DB_PATH}")
