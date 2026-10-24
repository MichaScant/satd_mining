"""Stage 1 — select & parse PRs from SWE-Review-Chat (Suzhen/SWE-Review-Chat).

- AI-agent PRs (created_by_type == ai) = study set.
- Human PRs sampled from the SAME repos = control.
- review_conversations (time-ordered array) exploded into review_events rows.

The dataset is ~1.08M PRs / ~11.4 GB decompressed, so we LAZY-scan the parquet and
collect only the filtered study/control sets (never load the whole thing into RAM).

Run:  python stage1_select.py [--limit N] [--control-ratio 1.0]

Schema (Suzhen/SWE-Review-Chat): verified columns are in COLMAP; review_conversations
entries carry type/timestamp/reviewer/reviewer_type/body/state/path/diff_hunk/thread_replies.
"""
import argparse
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import config
from db import store

# Verified against datasets-server schema for Suzhen/SWE-Review-Chat
COLMAP = {
    "repo": "repo_full_name",
    "repo_url": "repo_url",
    "pr_number": "pr_number",
    "author": "created_by",
    "created_by_type": "created_by_type",
    "state": "state",
    "merged_at": "merged_at",
    "created_at": "created_at",
    "title": "title",
    "review_conversations": "review_conversations",
}
# PR body is NOT in this dataset (no `body` column); circumstance labels fall back to
# review_conversations text + the PR title.


def _norm_type(login, type_str=None) -> str:
    """Reviewer/actor class for the conversational channel. Coding agent -> 'ai';
    automation (CI/deps/review bots) -> 'bot' (excluded from AI-admission counts); else 'human'."""
    kind, _ = agents.classify_actor(login, type_str)
    return {"coding_agent": "ai", "automation_bot": "bot"}.get(kind, "human")


def _event_kind(ev: dict) -> str:
    """Map SWE-Review-Chat entry -> our review_events.kind."""
    st = str(ev.get("state") or "").lower()
    if "changes_requested" in st or "changes requested" in st:
        return "changes_requested"
    if "approv" in st:
        return "approve"
    if ev.get("path") or ev.get("diff_hunk"):
        return "inline"
    return str(ev.get("type") or "comment").lower()


def _explode_reviews(conn, pr_id, conversations) -> int:
    if conversations is None:
        return 0
    n = 0
    for ev in conversations:
        if not isinstance(ev, dict):
            continue
        ts = ev.get("timestamp")
        store.add_review_event(
            conn, pr_id, ts=ts,
            actor=ev.get("reviewer"),
            actor_type=_norm_type(ev.get("reviewer"), ev.get("reviewer_type")),
            kind=_event_kind(ev),
            file_path=ev.get("path"),
            body=ev.get("body"),
        )
        n += 1
        # thread replies are also review interactions (no own timestamp -> inherit parent ts)
        for rep in (ev.get("thread_replies") or []):
            if not isinstance(rep, dict):
                continue
            store.add_review_event(
                conn, pr_id, ts=ts,
                actor=rep.get("reviewer"), actor_type=_norm_type(rep.get("reviewer"), rep.get("reviewer_type")),
                kind="reply", file_path=ev.get("path"), body=rep.get("body"),
            )
            n += 1
    return n


def run(limit=None, control_ratio=1.0, seed=42):
    c = COLMAP
    src = config.SWE_REVIEW_CHAT_PARQUET
    # support either a single file or a directory of shards
    glob = str(src if src.suffix == ".parquet" else (src / "**" / "*.parquet"))
    # native expression (no per-row Python UDF over ~1.08M rows). Study cohort is genuine
    # CODING AGENTS only (Copilot/Devin/Claude/...), matched on the PR-author login OR type;
    # automation (renovate/dependabot/CI) is tagged separately and excluded from both cohorts.
    ca = f"(?i)({agents.CODING_AGENT_RE.pattern})"
    au = f"(?i)({agents.AUTOMATION_RE.pattern})"
    login = pl.col(c["author"]).cast(pl.Utf8).fill_null("")
    atype = pl.col(c["created_by_type"]).cast(pl.Utf8).fill_null("")
    is_agent = login.str.contains(ca) | atype.str.contains(ca)
    is_auto = login.str.contains(au) | atype.str.contains(au)
    lf = pl.scan_parquet(glob).with_columns(
        pl.when(is_agent).then(pl.lit("ai"))
          .when(is_auto).then(pl.lit("automation"))
          .otherwise(pl.lit("human")).alias("_atype")
    )

    ai = lf.filter(pl.col("_atype") == "ai").collect()   # study = coding agents only
    projects = ai[c["repo"]].unique().to_list()
    print(f"study (coding-agent) PRs: {ai.height} across {len(projects)} repos")

    human = (lf.filter((pl.col("_atype") == "human") & pl.col(c["repo"]).is_in(projects))
               .collect())
    n_control = int(ai.height * control_ratio)
    if human.height > n_control:
        human = human.sample(n=n_control, seed=seed)
    print(f"control (human) PRs: {human.height}")

    conn = store.init_db()
    total_reviews = 0
    for cohort, sub in (("study", ai), ("control", human)):
        rows = sub.head(limit) if limit else sub
        for r in rows.iter_rows(named=True):
            pr_id = store.upsert_pr(
                conn, repo=r[c["repo"]], pr_number=int(r[c["pr_number"]]),
                repo_url=r.get(c["repo_url"]), author=r.get(c["author"]), author_type=r["_atype"],
                state=r.get(c["state"]), merged_at=r.get(c["merged_at"]),
                created_at=r.get(c["created_at"]), cohort=cohort, title=r.get(c["title"]),
            )
            total_reviews += _explode_reviews(conn, pr_id, r.get(c["review_conversations"]))
        conn.commit()
    print(f"wrote {min(limit or ai.height, ai.height) + min(limit or human.height, human.height)} PRs "
          f"and {total_reviews} review_events -> {config.DB_PATH}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap PRs per cohort (smoke test)")
    ap.add_argument("--control-ratio", type=float, default=1.0)
    run(**vars(ap.parse_args()))
