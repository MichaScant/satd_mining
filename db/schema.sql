-- AI Technical-Debt Mining Pipeline — SQLite schema
-- Every table keys on natural ids so re-running a stage is idempotent (INSERT OR IGNORE / upsert).
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Stage 1: PR selection + author labels (Suzhen / SWE-Review-Chat is source of truth)
CREATE TABLE IF NOT EXISTS prs (
    id          INTEGER PRIMARY KEY,
    repo        TEXT    NOT NULL,            -- "owner/name"
    repo_url    TEXT,
    pr_number   INTEGER NOT NULL,
    author      TEXT,
    author_type TEXT,                        -- 'ai' | 'human'  (from created_by_type)
    state       TEXT,
    merged_at   TEXT,
    created_at  TEXT,
    cohort      TEXT,                         -- 'study' (AI) | 'control' (human)
    title       TEXT,
    body        TEXT,
    UNIQUE (repo, pr_number)
);

-- Stage 1: time-ordered review conversation, exploded one row per entry
CREATE TABLE IF NOT EXISTS review_events (
    id         INTEGER PRIMARY KEY,
    pr_id      INTEGER NOT NULL REFERENCES prs(id),
    ts         TEXT,
    actor      TEXT,
    actor_type TEXT,                          -- 'ai' | 'human'
    kind       TEXT,                          -- comment | inline | approve | changes_requested
    file_path  TEXT,                          -- for inline comments
    body       TEXT,
    event_uid  TEXT NOT NULL UNIQUE           -- sha1(pr_id|ts|actor|kind|file_path|body) -> idempotent
);

-- Stage 2: commits fetched from GitHub per PR
CREATE TABLE IF NOT EXISTS commits (
    sha             TEXT PRIMARY KEY,
    pr_id           INTEGER NOT NULL REFERENCES prs(id),
    author_login    TEXT,
    committer_login TEXT,
    co_authors      TEXT,                     -- JSON array of Co-Authored-By trailers
    authored_at     TEXT,
    phase           TEXT,                     -- first_attempt | revision | human_applied
    author_class    TEXT,                     -- ai | human | conflict
    author_conflict INTEGER DEFAULT 0         -- 1 if Suzhen/account/trailers disagree
);

-- Stage 2: file-level patches (the patch body is archived to disk; patch_ref points at it)
CREATE TABLE IF NOT EXISTS file_changes (
    id        INTEGER PRIMARY KEY,
    sha       TEXT NOT NULL REFERENCES commits(sha),
    path      TEXT NOT NULL,
    patch_ref TEXT,                           -- relative path under data/raw_diffs/
    additions INTEGER,
    deletions INTEGER,
    UNIQUE (sha, path)
);

-- Stage 3a: SATD instances (comment_text_norm doubles as the cross-PR tracking identity)
CREATE TABLE IF NOT EXISTS satd_instances (
    id                INTEGER PRIMARY KEY,
    repo              TEXT NOT NULL,
    introducing_sha   TEXT,
    introducing_pr    INTEGER,
    path              TEXT NOT NULL,
    line_span         TEXT,                   -- "120-122"
    comment_text_norm TEXT NOT NULL,          -- markers/whitespace stripped (tracking identity)
    comment_text_raw  TEXT,                   -- literal snippet for git pickaxe (-S)
    keyword           TEXT,                   -- matched marker/phrase (FIXME|HACK|XXX|"workaround"|...)
    satd_type         TEXT,                   -- design | bug | refactoring  (ported keyword classifier)
    channel           TEXT,                   -- code_comment | conversation
    source_kind       TEXT,                   -- added_line_comment | pr_body | agent_reply | developer_request
    utterance_id      INTEGER,                -- review_events.id for conversational (channel=conversation)
    introducer_kind   TEXT,                   -- coding_agent | automation_bot | human  (who authored the debt)
    agent_name        TEXT,                   -- copilot | devin | claude | cursor | codex | ...
    committed_by      TEXT,                   -- agent | github_merge | human  (who put the commit in the repo)
    n_ai_revisits     INTEGER,                -- # coding-agent touches of the debt before resolution
    n_ai_revisit_prs  INTEGER,
    intro_patch_ref   TEXT,                   -- archived introducing patch (exact code AI added)
    intro_hunk        TEXT,                   -- diff hunk that introduced the SATD
    resolved_path     TEXT,
    fix_hunk          TEXT,                   -- diff hunk that removed/changed it (exact fix code)
    author_class      TEXT,
    circumstance      TEXT,                   -- first_attempt|revision|requested_by_developer|human_followup
    -- Stage 5 cross-PR tracking:
    anchor_merge_sha  TEXT,
    anchor_path       TEXT,
    anchor_line       INTEGER,
    status            TEXT,                   -- resolved|comment_deleted_only|file_deleted|self_resolved_premerge|alive|untrackable
    resolved_sha      TEXT,
    resolved_pr       INTEGER,
    resolved_at       TEXT,
    resolver_class    TEXT,                   -- ai | human  (headline: resolved-by-AI vs developer)
    censor_date       TEXT,                   -- for right-censored 'alive' instances
    survival_days     REAL,                   -- merge -> resolved (or -> censor) in days
    n_events          INTEGER,                -- total touching events (within + cross PR)
    n_distinct_prs    INTEGER,                -- distinct PRs that touched the instance
    -- who pointed it out first (AI/human, at file or commit level):
    first_pointer_class TEXT,                 -- ai | human
    first_pointer_level TEXT,                 -- commit (self-admission) | file (reviewer flag)
    first_pointer_ts    TEXT,
    -- if a human resolved it, how much AI was involved in the resolving PR:
    resolver_ai_commits    INTEGER,
    resolver_ai_discussion INTEGER,
    UNIQUE (repo, introducing_sha, path, comment_text_norm)
);

-- Stage 5: every later touch of an instance (not just the final fix)
CREATE TABLE IF NOT EXISTS satd_events (
    id          INTEGER PRIMARY KEY,
    instance_id INTEGER NOT NULL REFERENCES satd_instances(id),
    event_sha   TEXT NOT NULL,
    event_pr    INTEGER,
    ts          TEXT,
    kind        TEXT,                         -- modified | removed | file_deleted | reworded
    scope       TEXT,                         -- within_pr | cross_pr
    actor_class TEXT,                         -- ai | human
    UNIQUE (instance_id, event_sha, kind)
);

-- Stage 3b: quality-drop metrics, before/after per changed file
CREATE TABLE IF NOT EXISTS quality_deltas (
    id     INTEGER PRIMARY KEY,
    sha    TEXT NOT NULL,
    path   TEXT NOT NULL,
    tool   TEXT NOT NULL,                     -- jscpd|lizard|ruff|pylint|eslint|pmd|checkstyle|sonarqube
    metric TEXT NOT NULL,
    before REAL,
    after  REAL,
    UNIQUE (sha, path, tool, metric)
);

-- Stage 3c: reviewer flags linked to follow-up fixes
CREATE TABLE IF NOT EXISTS reviewer_flags (
    id             INTEGER PRIMARY KEY,
    pr_id          INTEGER NOT NULL REFERENCES prs(id),
    event_id       INTEGER REFERENCES review_events(id),
    category       TEXT,                      -- hack|workaround|duplication|missing_tests
    actor_type     TEXT,                      -- ai | human (who flagged)
    file_path      TEXT,                      -- set for inline (file-level) flags
    ts             TEXT,
    level          TEXT,                      -- file (inline) | review (PR-level)
    linked_fix_sha TEXT,                      -- nullable
    UNIQUE (pr_id, event_id, category)
);

-- Stage 2: resumability + reproducibility ledger (write before fetching anything)
CREATE TABLE IF NOT EXISTS fetch_log (
    id         INTEGER PRIMARY KEY,
    repo       TEXT NOT NULL,
    pr_number  INTEGER NOT NULL,
    fetched_at TEXT,
    status     TEXT,                          -- pending|ok|gone|too_large|rate_limited
    detail     TEXT,
    UNIQUE (repo, pr_number)
);

-- Stage 7/8: validation sample queues, wired in from day one
CREATE TABLE IF NOT EXISTS validation_queue (
    id         INTEGER PRIMARY KEY,
    queue      TEXT NOT NULL,                 -- flagged_satd|unflagged_comment|attribution|requested_by_dev|tracker_outcome
    ref_table  TEXT,
    ref_id     INTEGER,
    payload    TEXT,                          -- JSON snapshot for raters
    rater1     TEXT,
    rater2     TEXT,
    created_at TEXT,
    UNIQUE (queue, ref_table, ref_id)
);

CREATE INDEX IF NOT EXISTS ix_review_events_pr ON review_events(pr_id);
CREATE INDEX IF NOT EXISTS ix_commits_pr       ON commits(pr_id);
CREATE INDEX IF NOT EXISTS ix_file_changes_sha ON file_changes(sha);
CREATE INDEX IF NOT EXISTS ix_satd_instances_repo ON satd_instances(repo);
CREATE INDEX IF NOT EXISTS ix_satd_events_inst  ON satd_events(instance_id);
