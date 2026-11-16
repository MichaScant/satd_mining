"""Stage 3 (AIDev) — extract code-comment SATD DIRECTLY from AIDev's local parquet (no GitHub
fetch, no token, no rate limit). Uses the SAME detector and SAME satd_instances schema as
stage3_satd_scan.py, so AIDev SATD is directly comparable to Suzhen's — only the source of the
diffs differs (AIDev's pr_commit_details.parquet instead of archived GitHub patches).

  python stage3_aidev.py --aidev-dir data/aidev --exclude copilot [--limit N] [--chunk 20000]

Reads pull_request.parquet (id -> repo/number/agent) + pr_commit_details.parquet (sha, pr_id,
filename, patch, message, author), filters to the chosen agents, runs the SATD detector over each
added-line comment, writes satd_instances (channel='code_comment', introducer_kind='coding_agent',
agent_name = AIDev's authoritative label). PRs must already be in `prs` (run stage1_aidev first) so
the instances join back to cohort='aidev'.
"""
import argparse
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import satd_detect as sd
from db import store
from stage3_satd_scan import added_comments          # identical hunk/comment extraction
from stage1_aidev import AIDEV_AGENT_MAP, _PRURL      # identical agent mapping


def _repo_num(url):
    m = _PRURL.search(str(url or ""))
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _scan_agent(conn, details, name, prs_list, per_agent, chunk):
    """Scan one agent's PRs until `per_agent` SATD instances are found (or PRs exhausted)."""
    idmap = {pid: (repo, num) for pid, repo, num in prs_list}
    ids = list(idmap)
    cols = ["pr_id", "sha", "filename", "patch", "message"]
    found = 0
    print(f"=== {name}: {len(ids)} PRs available, target {per_agent} SATD ===")
    for i in range(0, len(ids), chunk):
        if found >= per_agent:
            break
        batch = ids[i:i + chunk]
        sub = (pl.scan_parquet(details)
               .filter(pl.col("pr_id").is_in(batch) & pl.col("patch").is_not_null())
               .select(cols).collect())
        for r in sub.iter_rows(named=True):
            if found >= per_agent:
                break
            meta = idmap.get(r["pr_id"])
            if not meta:
                continue
            repo, num = meta
            path = r["filename"]
            if not sd.is_source_file(path):
                continue
            for line_no, raw, hunk in added_comments(r["patch"] or ""):
                kw = sd.detect(raw)
                if not kw:
                    continue
                norm = sd.normalize(raw)
                if not norm:
                    continue
                store.upsert_satd_instance(
                    conn, repo=repo, introducing_sha=r["sha"], path=path,
                    comment_text_norm=norm, comment_text_raw=raw.strip()[:200],
                    introducing_pr=num, line_span=str(line_no), keyword=kw,
                    satd_type=sd.classify_type(raw), channel="code_comment",
                    source_kind="added_line_comment", author_class="ai",
                    introducer_kind="coding_agent", agent_name=name, committed_by="agent",
                    intro_hunk=hunk[:1500],
                )
                found += 1
                if found >= per_agent:
                    break
        conn.commit()
        print(f"   {name}: {found}/{per_agent} SATD after {min(i + chunk, len(ids))} PRs")
    print(f"   {name}: DONE — {found} SATD" + ("" if found >= per_agent else "  (exhausted PRs)"))
    return found


def run(aidev_dir="data/aidev", agents_filter=None, exclude=None, per_agent=2000, chunk=5000):
    d = Path(aidev_dir)
    pr = pl.read_parquet(d / "pull_request.parquet", columns=["id", "html_url", "agent"])

    want = set(AIDEV_AGENT_MAP)
    if agents_filter:
        pick = {a.strip().lower() for a in agents_filter.split(",") if a.strip()}
        want = {l for l, n in AIDEV_AGENT_MAP.items() if n in pick}
    elif exclude:
        drop = {a.strip().lower() for a in exclude.split(",") if a.strip()}
        want = {l for l, n in AIDEV_AGENT_MAP.items() if n not in drop}

    from collections import defaultdict
    by_agent = defaultdict(list)   # agent_name -> [(pr_id, repo, pr_number), ...]
    for row in pr.iter_rows(named=True):
        lbl = str(row["agent"] or "").strip().lower()
        if lbl not in want:
            continue
        repo, num = _repo_num(row["html_url"])
        if repo:
            by_agent[AIDEV_AGENT_MAP[lbl]].append((row["id"], repo, num))
    print("PRs per agent in scope:", {k: len(v) for k, v in by_agent.items()})

    conn = store.connect(); store.init_db(conn)
    details = d / "pr_commit_details.parquet"
    grand = 0
    for name in sorted(by_agent, key=lambda a: -len(by_agent[a])):
        grand += _scan_agent(conn, details, name, by_agent[name], per_agent, chunk)
    print(f"\ndone: {grand} AIDev code-comment SATD instances total")
    print("by agent (aidev cohort, code channel):")
    for row in conn.execute(
            "SELECT i.agent_name, COUNT(*) n FROM satd_instances i "
            "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
            "WHERE p.cohort='aidev' AND i.channel='code_comment' "
            "GROUP BY i.agent_name ORDER BY n DESC"):
        print(f"   {str(row[0]):10} {row[1]}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--aidev-dir", default="data/aidev")
    ap.add_argument("--agents", dest="agents_filter", default=None, help="keep ONLY these agents")
    ap.add_argument("--exclude", default=None, help="keep all agents EXCEPT these, e.g. copilot")
    ap.add_argument("--per-agent", dest="per_agent", type=int, default=2000,
                    help="stop each agent once this many SATD instances are found (default 2000)")
    ap.add_argument("--chunk", type=int, default=5000, help="PRs per parquet-scan batch (memory knob)")
    run(**vars(ap.parse_args()))
