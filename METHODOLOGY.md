# AI Technical-Debt Mining — Methodology & Tracking

How the pipeline detects self-admitted technical debt (SATD) introduced by AI coding
agents, attributes who introduced/merged it, and tracks it over time — including **how
often an agent revisits debt it created** and whether that debt is ever resolved.

---

## 1. What we measure

- **RQ1 — Do agents document their technical debt, and how often?** (self-admission rate in code *and* in conversation)
- **RQ2 — How often do agents revisit their own technical debt?** (returns to a debt they introduced, before it is resolved)
- **Supporting:** who introduced it, who merged it, who pointed it out first, whether it was resolved (and by AI or human), and how long it survived.

## 2. Data source

`Suzhen/SWE-Review-Chat` (HuggingFace): ~1.08M agent-authored pull requests, each with its
full, time-ordered `review_conversations` thread and per-utterance actor labels. Repository
code and commit/merge history are fetched on demand from the GitHub API. No PR `body` column
exists in the dataset, so conversational signals come from the PR title + review utterances.

## 3. Pipeline at a glance

| Stage | File | What it does |
|---|---|---|
| 1. Select | `stage1_select.py` | Lazily scans the parquet; study cohort = **coding-agent** PRs, control = human PRs from the same repos; explodes `review_conversations` → `review_events`. |
| 2. Fetch | `stage2_fetch.py` | Per PR, pulls commits + file patches from GitHub (rate-limit aware, resumable per-PR via `fetch_log`); archives each patch to `data/raw_diffs/`. |
| 3a. Code SATD | `stage3_satd_scan.py` | **Channel A** — detects debt comments on *added* lines, classifies type, stamps the introducer, stores the introducing code block. |
| 3b. Conversational SATD | `stage3b_conversational.py` | **Channel B** — detects debt admissions in PR text + agent/developer utterances (no GitHub needed). |
| 3c. Reviewer flags | `stage3c_reviewer_flags.py` | Records reviewers *flagging* debt the author didn't admit, and computes the **first-pointer** per instance. |
| 5. Track | `stage5_track.py` | Clones each repo, follows every later touch of the debt (git pickaxe), classifies who touched it, and determines resolution + survival. **This is where revisits are counted.** |
| — Attribution backfill | `backfill_attribution.py` | Stamps agent attribution + revisit counts onto pre-existing rows from data already in the DB. |
| — Report | `report.py` | Prints all stats, writes `report.txt` + `report_metrics.csv`, and renders the graphs. |

Shared detection logic lives in `satd_detect.py` (keyword/phrase core, type classifier) and
`agents.py` (who counts as a coding agent).

## 4. Who is an "AI agent"? (`agents.py`)

A key correctness step. The raw dataset tags *any* bot as "AI", but dependency/CI/release
automation (renovate, dependabot, github-actions, …) does not author feature code. We
separate three actor kinds via one classifier (`agents.classify_actor`):

- **`coding_agent`** — writes code: Copilot, Devin, Claude, Cursor, Codex, Aider, OpenHands, …
- **`automation_bot`** — manages the repo, excluded from the AI cohort.
- **`human`** — a person.

Only `coding_agent` debt counts as "AI-generated." Every SATD instance stores `introducer_kind`,
`agent_name` (which agent), and `committed_by` (`agent` / `github_merge` / `human` — i.e. who
actually put the commit in the repo, so we can isolate "AI generated it, a human merged it").

## 5. Detecting *documented* debt — the two channels

**Channel A — code comments (`stage3_satd_scan.py`).** For each fetched commit patch we keep
only `+` (added) lines, extract comment text, and match the Potdar & Shihab / Maldonado debt
phrase set plus the `FIXME`/`HACK`/`XXX` markers. A **bare `TODO` does not count** — it only
qualifies alongside a real debt phrase. We scan source files only (CI YAML/config/docs
excluded). Each hit is classified **design / bug / refactoring** (ported keyword classifier)
and the exact **introducing diff hunk** is stored (`intro_hunk`, plus `intro_patch_ref`).

**Channel B — conversation (`stage3b_conversational.py`).** The same phrase core plus a
conversational-deferral list ("for now", "follow-up", "out of scope", "hard-coded for now", …)
is run over the PR title and every utterance, after stripping fenced/inline code (so a quoted
`// TODO` isn't miscounted). Hits are tagged `agent_reply`, `developer_request`, or `pr_body`.

Both channels write to one `satd_instances` table with a `channel` column, so documentation
is measured as a rate in each: **% of agent commits with a debt comment** and **% of agent
utterances that admit debt**.

## 6. Tracking over time — the core of "how often they revisit" (`stage5_track.py`)

For every code-comment instance:

1. **Anchor** the debt at the introducing PR's merge commit (confirm it survived to merge; if
   it was removed before merge → `self_resolved_premerge`).
2. **Find every later touch** with a git *pickaxe* search (`git log -S` on the literal comment)
   since the merge date — each commit whose relationship to the comment changes is an **event**.
   We capture both **within-PR** touches (from archived patches, robust to squash-merge) and
   **cross-PR** touches (from the clone).
3. **Classify each touch's actor** (`agents.classify_actor` on the touching commit/PR) → every
   event is stored in `satd_events` with an `actor_class` (`ai` = a coding agent, else `human`),
   a `kind` (`modified` / `removed` / `file_deleted` / `reworded`), and a `scope`
   (`within_pr` / `cross_pr`).
4. **Count revisits.** `n_ai_revisits` = number of `satd_events` for that instance with
   `actor_class='ai'`; `n_ai_revisit_prs` = distinct PRs among them. **That is the per-instance
   "how many times an agent came back to this debt" counter**, and RQ2 aggregates it.
5. **Resolution + the fix code block.** The first `removed`/`file_deleted` event sets the final
   status and records the **fix diff hunk** (`fix_hunk`, `resolved_path`, `resolved_sha/pr`) and
   the resolver's class. A comment removed with no real code change → `comment_deleted_only`
   (silent carry, *not* a genuine fix).
6. **Survival.** Days from merge → resolution (or → censor date if still alive), feeding the
   Kaplan–Meier curve.

Status taxonomy: `resolved` · `comment_deleted_only` · `file_deleted` · `self_resolved_premerge`
· `alive` (right-censored) · `untrackable`.

## 7. How each headline number is computed

- **RQ1 documentation (code):** distinct coding-agent commits that introduced ≥1 SATD comment ÷ all coding-agent commits.
- **RQ1 documentation (conversation):** agent utterances admitting debt ÷ all agent utterances.
- **RQ2 revisit rate:** agent-introduced instances with `n_ai_revisits > 0` ÷ tracked agent-introduced instances (mean/distribution of `n_ai_revisits` reported alongside).
- **Who pointed it out first:** self-admission in code (`commit` level) vs earliest reviewer inline flag (`file` level), AI vs human.
- **Who resolved it / success:** `resolver_class` + status; success = genuine code-change resolution vs silent comment deletion.
- **Humans merging AI debt:** agent-introduced instances with `committed_by IN ('human','github_merge')`, cross-referenced with human approvals.
- **Survival:** Kaplan–Meier over `survival_days` with censoring.

## 8. What's stored (key columns on `satd_instances`)

`repo, introducing_sha, introducing_pr, path, line_span` · `channel, source_kind, satd_type,
keyword` · `introducer_kind, agent_name, committed_by, author_class` · `intro_hunk,
intro_patch_ref` (code AI introduced) · `status, resolved_sha, resolved_pr, resolver_class,
resolved_path, fix_hunk` (code where it was fixed) · `n_events, n_distinct_prs, n_ai_revisits,
n_ai_revisit_prs` · `first_pointer_class/level/ts` · `survival_days, censor_date`. Every touch
is in `satd_events`; raw patches are archived under `data/raw_diffs/`.

## 9. Where the outputs are

All paths relative to `satd_mining/`:

- **Graphs → `graphs/`**
  - `graphs/agent_behavior.png` — RQ1 + RQ2 in one: documents-in-code, documents-in-conversation, revisits-own-debt
  - `graphs/conversational_rate.png` — AI vs human conversational admission rate
  - `graphs/satd_by_type.png` — design / bug / refactoring split
  - `graphs/first_pointer.png` — who flagged debt first (AI/human × commit/file)
  - `graphs/introduced_vs_resolved.png` — AI vs human as introducer vs resolver
  - `graphs/status_success.png` — resolution-status breakdown
  - `graphs/survival_km.png` — Kaplan–Meier survival curve
- **Tables / text**
  - `report.txt` — full console report
  - `report_metrics.csv` — every headline metric as `section, metric, value`
  - `ai_generated_satd.csv` — the filtered AI-generated-debt instance list (human-merged flagged)
- **Database:** `db/satd_mining.db` (schema in `db/schema.sql`)

## 10. Reproduce

```bash
# one-time: deps + schema + dataset
bash setup.sh

# full pipeline
python3 stage1_select.py
GITHUB_TOKEN=... python3 stage2_fetch.py
python3 stage3_satd_scan.py          # Channel A (code)
python3 stage3b_conversational.py    # Channel B (conversation)
python3 stage3c_reviewer_flags.py
GITHUB_TOKEN=... python3 stage5_track.py   # tracking + revisits + resolution
python3 backfill_attribution.py
python3 report.py                    # stats + graphs + csv

# or, after a fetch, the post-fetch stages in one shot:
bash run_rest.sh
```
