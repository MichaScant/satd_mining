"""Stage 3d — detect GitHub ISSUE cross-references that track a SATD (Channel C, local half).

Background / why this is new
----------------------------
We already have SATD *content* detectors (satd_detect.detect / detect_conversational /
is_debt_removal / _DEBT_TERM). We did NOT have anything that extracts GitHub issue references
(`#123`, `owner/repo#123`, issue URLs) — the only prior mention of "issue" in the codebase is the
word inside debt phrases ("fix issue"). So this is a net-new detector; it REUSES satd_detect for the
"is this reference actually about the debt" check so the text-match half stays literature-grounded
rather than ad-hoc keyword soup.

What it does (the LOCAL, zero-API half of the issue channel)
------------------------------------------------------------
For every AIDev PR that already carries >=1 SATD instance, scan the PR title/body and every
conversation turn for issue references, and record a candidate link ONLY when the reference sits in
a genuine debt context. This is direction (b) PR->issue of the validity rule:

    a link is valid only via an explicit NUMERIC cross-reference, never bare word-matching.
    (b) PR->issue  : the PR references the issue number, in debt context (handled here, locally).
    (a) issue->PR  : the issue references the PR back (needs the GitHub API — stage3e, later).

Validity enforced here (PR side):
  1. a numeric issue reference is present (#N | owner/repo#N | .../issues/N), AND
  2. the text unit carrying it also fires a SATD signal (detect_conversational OR _DEBT_TERM)
     -> "the issue is referenced *because of* the debt", not a coincidental "#1".
Bare references with no debt context are counted-and-dropped (reported, not stored).

Intent is tagged from the verb near the reference:
  deferral   : open/create/file/raise/log/track/defer/split/move/follow-up/see/capture  (debt punted
               to an issue — the B<->C deferral link we care about)
  resolution : close/fix/resolve/address                                                (PR closes an
               existing tracked issue)
  mention    : neither verb nearby

Output: table `aidev_issue_links` (candidate links; state/closed_at filled later by the API stage).
This stage makes NO network calls — its hit count tells us whether the issue channel is worth fetching.

  python stage3d_issue_refs.py [--cohort aidev]
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import satd_detect as sd
from db import store

# ---- issue-reference patterns (numeric cross-reference only) -------------------------------------
_RE_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)", re.I)
_RE_XREPO = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)\b")          # owner/repo#123
_RE_SAME = re.compile(r"(?:^|[\s(\[])#(\d+)\b")                 # #123 in this repo
# verb cues in a small window BEFORE the reference
_DEFER = re.compile(r"(open|creat|file|rais|log|track|defer|split|move|follow[\s-]?up|see|captur|"
                    r"spin\s+off|carve\s+out)\w*", re.I)
_RESOLVE = re.compile(r"(close|fix|resolv|address|complet)\w*", re.I)


def _intent(text, pos):
    """Classify a reference by the verb in the ~60 chars before it."""
    window = text[max(0, pos - 60):pos]
    if _RESOLVE.search(window):
        return "resolution"
    if _DEFER.search(window):
        return "deferral"
    return "mention"


def _refs(text, pr_repo):
    """Yield (issue_repo, issue_number, intent) for every numeric issue reference in `text`.
    De-dups within the text; same-repo refs inherit the PR's repo."""
    seen = set()
    out = []
    for m in _RE_URL.finditer(text):
        key = (f"{m.group(1)}/{m.group(2)}", int(m.group(3)))
        if key not in seen:
            seen.add(key); out.append((key[0], key[1], _intent(text, m.start())))
    for m in _RE_XREPO.finditer(text):
        key = (m.group(1), int(m.group(2)))
        if key not in seen:
            seen.add(key); out.append((key[0], key[1], _intent(text, m.start())))
    for m in _RE_SAME.finditer(text):
        key = (pr_repo, int(m.group(1)))
        if key not in seen:
            seen.add(key); out.append((key[0], key[1], _intent(text, m.start())))
    return out


def _has_debt(text):
    """The text unit must carry a SATD signal for the reference to count as debt-related."""
    stripped = sd.strip_code(text)
    return bool(sd.detect_conversational(stripped) or sd._DEBT_TERM.search(stripped))


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aidev_issue_links (
            id INTEGER PRIMARY KEY,
            repo TEXT, pr_number INTEGER,
            issue_repo TEXT, issue_number INTEGER,
            direction TEXT,              -- 'pr_to_issue' (this stage) | 'issue_to_pr' (API stage)
            intent TEXT,                 -- 'deferral' | 'resolution' | 'mention'
            source_kind TEXT,            -- 'pr_body' | 'utterance'
            utterance_id INTEGER,        -- review_events.id when from a turn
            satd_context INTEGER,        -- 1 = debt signal present in the text unit (always 1 if stored)
            snippet TEXT,
            is_issue INTEGER,            -- filled by API stage: 1 real issue / 0 PR / NULL unknown
            issue_ref_confirmed INTEGER, -- filled by API stage: issue references the PR back
            state TEXT, closed_at TEXT,
            UNIQUE(repo, pr_number, issue_repo, issue_number, source_kind, utterance_id)
        )""")
    conn.commit()


def _snip(text, repo, num):
    m = re.search(rf"#{num}\b", text)
    i = m.start() if m else 0
    return text[max(0, i - 50):i + 50].replace("\n", " ").strip()[:160]


def run(cohort="aidev"):
    conn = store.connect()
    _ensure_table(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_re_prid ON review_events(pr_id)")
    # one-time cleanup of pr_body duplicates from earlier runs (NULL utterance_id defeated the UNIQUE
    # constraint since SQLite treats NULLs as distinct). DELETE dupes FIRST — treating NULL and -1 as
    # the same logical row (COALESCE) and keeping the validated one (or lowest id) so stage3e's fetched
    # state survives — THEN normalize the survivor to -1 (now at most one per key, so no collision).
    conn.execute("""DELETE FROM aidev_issue_links WHERE id NOT IN (
        SELECT id FROM (SELECT id, ROW_NUMBER() OVER (
            PARTITION BY repo, pr_number, issue_repo, issue_number, source_kind, COALESCE(utterance_id, -1)
            ORDER BY (valid IS NOT NULL) DESC, id ASC) rn FROM aidev_issue_links) WHERE rn=1)""")
    conn.execute("UPDATE aidev_issue_links SET utterance_id=-1 "
                 "WHERE source_kind='pr_body' AND utterance_id IS NULL")
    conn.commit()

    # PRs (in this cohort) that carry at least one SATD instance
    pr_rows = conn.execute(
        "SELECT DISTINCT p.id AS pr_dbid, p.repo, p.pr_number, p.title, p.body "
        "FROM prs p JOIN satd_instances i ON i.repo=p.repo AND i.introducing_pr=p.pr_number "
        "WHERE p.cohort=?", (cohort,)).fetchall()
    print(f"{len(pr_rows)} {cohort} PRs carry SATD — scanning for issue references ...", flush=True)

    stats = Counter()
    by_agent = defaultdict(Counter)
    kept = dropped = 0

    def consider(repo, pr_number, text, source_kind, utt_id, agent):
        nonlocal kept, dropped
        if not text:
            return
        refs = _refs(text, repo)
        if not refs:
            return
        debt = _has_debt(text)
        for issue_repo, issue_number, intent in refs:
            if not debt:                      # numeric ref present but NO debt context -> invalid
                dropped += 1
                continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO aidev_issue_links "
                    "(repo, pr_number, issue_repo, issue_number, direction, intent, source_kind, "
                    " utterance_id, satd_context, snippet) VALUES (?,?,?,?, 'pr_to_issue', ?,?,?,1,?)",
                    (repo, pr_number, issue_repo, issue_number, intent, source_kind, utt_id,
                     _snip(text, issue_repo, issue_number)))
                if cur.rowcount:                   # 1 = inserted, 0 = duplicate ignored
                    kept += 1
                    stats[intent] += 1
                    if agent:
                        by_agent[agent][intent] += 1
            except Exception:
                pass

    # agent per PR (authoring agent) for the per-agent breakdown
    agent_of = {}
    for r in conn.execute("SELECT repo, pr_number, agent FROM prs WHERE cohort=?", (cohort,)):
        agent_of[(r["repo"], r["pr_number"])] = r["agent"]

    for n, p in enumerate(pr_rows, 1):
        ag = agent_of.get((p["repo"], p["pr_number"]))
        body = " ".join(x for x in (p["title"], p["body"]) if x)
        consider(p["repo"], p["pr_number"], body, "pr_body", -1, ag)   # -1 sentinel (not NULL) so the
                                                                        # UNIQUE constraint dedupes re-runs
        for e in conn.execute("SELECT id, body FROM review_events WHERE pr_id=? AND body IS NOT NULL",
                              (p["pr_dbid"],)):
            consider(p["repo"], p["pr_number"], e["body"], "utterance", e["id"], ag)
        if n % 5000 == 0:
            conn.commit(); print(f"  {n}/{len(pr_rows)} PRs | kept {kept} links | dropped {dropped} "
                                 f"(ref but no debt context)", flush=True)
    conn.commit()

    distinct_issues = conn.execute(
        "SELECT COUNT(DISTINCT issue_repo || '#' || issue_number) FROM aidev_issue_links").fetchone()[0]
    print(f"\ndone. {kept} candidate PR->issue links in debt context "
          f"({dropped} numeric refs dropped for no debt context).")
    print(f"  by intent: {dict(stats)}")
    print(f"  distinct issues to fetch (API stage): {distinct_issues}")
    if by_agent:
        print("  by authoring agent (intent split):")
        for ag in sorted(by_agent, key=lambda a: -sum(by_agent[a].values())):
            print(f"    {str(ag):18} {dict(by_agent[ag])}")
    print("\nNext: stage3e (API) fetches each issue's state/closed_at, confirms the issue references "
          "the PR back (direction a) or was opened to address the SATD (direction b), and filters out "
          "numbers that are actually PRs.")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default="aidev")
    run(**vars(ap.parse_args()))
