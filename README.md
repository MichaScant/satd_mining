# AI Technical-Debt Mining Pipeline

Selects PRs from **SWE-Review-Chat** (Suzhen's dataset), fetches their code from GitHub,
detects technical debt, labels who caused it and when, then tracks each SATD comment
across later commits/PRs until it's resolved, carried, or the window ends.

Design locks (per the build plan): SWE-Review-Chat is the source of truth for PR
selection + author labels; AIDev supplies published baselines only (not a dependency);
SATD detection runs on **added lines only**; everything is batch, resumable, idempotent.

## Layout
```
satd_mining/
  config.py            paths, GitHub token, keywords, baselines, tool versions
  db/schema.sql        9-table SQLite schema (keyed on natural ids)
  db/store.py          init + idempotent upserts
  stage1_select.py     SWE-Review-Chat parquet -> prs + review_events
  stage2_fetch.py      GitHub fetch (commits+patches), resume + archive + fetch_log
  stage3_satd_scan.py  SATD scan on added lines -> satd_instances (+ validation queue)
  data/raw_diffs/      archived patches (reproducibility)
  data/clones/         full-history clones for Stage 5
  validation/          rater exports (Stage 8)
  notebooks/           analysis over the store (Stage 9)
```

## Setup
```bash
cd satd_mining
pip install -r requirements.txt
python db/store.py                 # create the SQLite schema
export GITHUB_TOKEN=ghp_...         # for Stage 2 (5k/hr; 60/hr without)
```

## Datasets to download (see "Download instructions" below)
- `data/swe_review_chat.parquet`  (or set `$SWE_REVIEW_CHAT`) — **required**
- `data/aidev/`                    (or set `$AIDEV_DIR`) — baselines only, optional

## Run (build order)
```bash
python stage1_select.py --limit 50           # smoke: 50 PRs/cohort
python stage2_fetch.py  --limit 50           # fetch their commits + patches
python stage3_satd_scan.py                   # first SATD numbers vs 2.6% baseline
```

## Build-order status
| # | Stage | Status |
|---|---|---|
| 1 | Store + Stage 1 (select/parse) | ✅ implemented |
| 2 | Fetcher (resume + archive + fetch_log) | ✅ implemented |
| 3a | SATD scan (added lines) | ✅ implemented |
| 4 | Phase + circumstance labels | ⏳ phase done in fetch; circumstance rules TODO |
| 5 | **Cross-PR tracker** (clones + pickaxe) | ⏳ next, biggest piece |
| 3b | Quality-drop tier | ⏳ TODO (independent) |
| 3c | Reviewer-flag tier + flag→fix linking | ⏳ TODO |
| 8 | Validation draws + κ tooling | ⏳ queues wired; κ TODO |
| 9 | Analysis notebooks | ⏳ TODO |

## Notes
- `author_class` is logged with a `conflict` flag when Suzhen label / committer account /
  co-author trailers disagree — never auto-resolved (per the plan).
- Unfetchable PRs are recorded in `fetch_log` as `gone`/`too_large`/`rate_limited`, never
  silently skipped.
- `comment_text_norm` is both the dedup key and the Stage-5 tracking identity.
