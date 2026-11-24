# SATD Detection & Classification Methodology (AIDev cohort)

This document records **exactly how each classification in the pipeline is made** — the rules, the
keyword/regex lists, and real examples pulled from the data — so every reported number is defensible.
All rules live in the cited source files; examples are verbatim from `db/satd_mining.db`.

Pipeline order: `stage3_aidev` (code SATD) → `stage3b` (conversation SATD) → `stage3c` (reviewer
flags) → `stage3d/3e/3f` (issue channel) → `stage5*` (resolution) → `analyze` + `persist_spotter`
(circumstance / spotter / how_spotted) → `stage6` (per-SATD outcome) → graphs.

Unit of analysis: a **SATD instance** (`satd_instances` row). Each carries a `channel`
(`code_comment` | `conversation`), a `source_kind`, and — after classification — `spotter`,
`how_spotted`, `outcome`, `resolved_how`, `n_revisits_resolve`.

---

## 1. What counts as SATD (debt detection) — `satd_detect.py`

**Code comments (`detect`)** — a comment is SATD if it contains a strong marker, or a curated
Potdar & Shihab / Maldonado & Shihab debt phrase. A **bare `TODO` is *not* SATD** (too noisy) unless a
debt phrase co-occurs.

- Strong markers (debt on their own): `STRONG_MARKER_RE = \b(FIXME|HACK|XXX)\b`
- Debt phrases (`DEBT_PHRASES`, matched case-insensitive): `hack`, `hacky`, `kludge`, `work[-/ ]around`,
  `temp(orary)`, `for now`, `for the time being`, `quick (and dirty|fix)`, `band-aid`, `dirty fix`,
  `ugly`, `stupid`, `nasty`, `messy`, `crap`, `not sure`, `don't know/understand`, `no idea`,
  `should (refactor|fix|rewrite|clean|be removed)`, `needs (to be) (fixed|refactor|rework|cleaned|revisit)`,
  `needs work`, `get rid of`, `revisit this`, `come back to`, `this is (wrong|broken|a mess|problematic)`,
  `not (yet) (implemented|supported|done|finished)`, `placeholder`, `stub`, `will break`, `is broken`,
  `remove this`, `rewrite this`, `giving up`.

**Conversation (`detect_conversational`)** — strips fenced/inline code first (so a quoted ``// TODO``
isn't counted), then applies the code rules **plus** a conversational-deferral list `DEFERRAL_PHRASES`:
`for now`, `follow-up`, `out of scope (for this pr)`, `(will|I'll) (address|handle|fix|do) … later`,
`address … in a (later|future|follow)`, `didn't have time`, `skip(ping)`, `known limitation`,
`leave (this/it) for`, `revisit later`, `come back to this`, `hard-cod(e/ed/ing)`,
`left … as a follow(-up)`, `not in (this/scope)`.

**Precision gate for self-admissions (`is_self_admission`)** — used by `analyze` for the high-precision
noticing set. A sentence counts only if it is **first-person about the author's own change**
(`_FIRST_PERSON`: I / I've / I'll / we / my / our …), carries a **leaving-debt cue** (`_INTRO_CUE`), and
is **not** describing a fix/removal (`_COMPLETED_FIX` guard) and **not** user-doc/instructional framing.

> Example SATD (code, strong phrase match): `"Placeholder for nc5 system - needs MTK v10 compatible
> implementation"` → matched keyword `placeholder`. `"Should throw \"Not implemented\" exception"` →
> `not implemented`.

---

## 2. Who spotted it — `spotter` ∈ {agent, dev, reviewbot} — `agents.py`, `stage3b`, `persist_spotter.py`

Actor of each conversation turn is classified by account login:
- **Coding agent** (`CODING_AGENT_RE`): `copilot`, `devin`, `claude|anthropic`, `cursor`, `codex`,
  `aider`, `openhands|opendevin`, `sweep`, `google-labs-jules|jules[bot]`, `sourcegraph|cody`,
  `gpt-engineer`, `codegen`.
- **AI review bot** (`AI_REVIEWER_RE`, checked *before* coding-agent): `coderabbit`, `greptile`,
  `gemini-code-assist`, `sourcery`, `qodo`, `cubic-dev-ai`, `copilot-pull-request-reviewer`, `bito`,
  `ellipsis-dev`, `pr-agent`, `codeant`, `korbit`, `entelligence`, `codescene`, `qodana`, … (reviewers
  *review*, they don't author the PR).
- **Automation** (`AUTOMATION_RE`): renovate, dependabot, github-actions, codecov, netlify, `[bot]`, …
- Otherwise **human**.

`spotter` is then assigned by channel/source:
- `code_comment` → **agent** (the agent wrote the debt into code it authored).
- conversation `agent_reply` / `pr_body` → **agent** (self-admission).
- conversation `developer_request` → **dev** (a human raised it).
- conversation `ai_reviewer_flag` → **reviewbot**.

**Review-bot content under an agent account** (`is_review_bot_post`): Bugbot/Devin-Review/Codex-Review
etc. post *reviews* from the coding agent's own bot account (e.g. `cursor[bot]`, `devin-ai-integration[bot]`).
These are detected by content and routed to **reviewbot**, never counted as the agent noticing its own
debt. Signatures (`_REVIEW_BOT_RE`): `bugbot`, `cursor bugbot`, `devin review`, `codex review`, `greptile`,
`has reviewed your changes`, `found N (potential) (issue|bug)`, `(low|medium|high|critical) severity`,
`pr_ai_review`, `ai-prove-fix`, `ai pr review`, `/ai-review`, `<!-- description start`, `devin-review-comment`.
Pure noise (PR summaries, validation evidence, CI, `[skip ci]`, `can help with this pull request`;
`_NOISE_RE`) is excluded entirely.

> Example reviewbot-spotted (posted under the agent's account, routed out of "agent"): a Cursor/Devin
> review post such as `"… Add comprehensive decomposition tree logging to identify error points …"`
> (`is_review_bot_post=True`).

---

## 3. How the AGENT spotted it — `how_spotted` ∈ {spontaneous, dev_elicited, reviewbot_elicited}

Computed only for `spotter='agent'` (see `persist_spotter._how`, mirrored in `analyze.trigger`). Priority:

1. **reviewbot_elicited** — a review bot pointed at *this* debt *before* the agent admitted it
   (`circumstance` ∈ ai_reviewer-*). Highest priority (debt-specific).
2. **dev_elicited** — either an in-thread developer signal preceded the admission, **or the PR was
   opened by a human developer** (the dev directed the agent — a dev-created PR is not spontaneous).
3. **spontaneous** — none of the above: an agent-bot-opened PR with no developer signal at all.

**In-thread signal (`analyze.trigger`).** Scope: it scans **every turn earlier in the PR, by timestamp**
(PR-wide) — it does **not** require the dev/reviewer to be on the same file or in the same comment thread,
only *before* the admission. For **code SATD** the "before" cut is the introducing commit date (recovered
from `aidev_commit_dates`, since AIDev discards commit dates — see §7). It labels the earliest matching
cue, in this priority:

*Developer (human) turns:*
- `dev named debt` — the turn matches the **SATD phrase set** (`detect_conversational`, §1).
- `dev alluded to error` — matches **`ERR`**: `doesn't work`, `not working`, `broken`, `fail*`, `error`,
  `bug`, `wrong`, `incorrect`, `crash*`, `exception`, `not right`, `unexpected`, `regression`, `typo`,
  `not correct`, `misses`.
- `dev changes_requested` — the review state is CHANGES_REQUESTED.
- `dev raised concern/question` — matches **`CONCERN`**: a literal `?`, or `why`, `what`, `hmm+`, `weird`,
  `odd`, `strange`, `confus*`, `unclear`, `not sure`, `should (we|this|it|you|be)`, `could you`, `can you`,
  `please`, `consider`, `instead of`, `rather than`, `needs? to`, `remove this`, `change this`, `revert`.
- `neutral dev comment before` — any prior human turn that matched none of the above.

*Review-bot turns* (same scan, `actor_type='ai_reviewer'`):
- `ai_reviewer named debt` — the reviewer turn matches **`has_debt` (SATD phrase) OR `ERR`** (for the bot
  these two are merged into "named the debt").
- `ai_reviewer raised concern` — matches **`CONCERN`**.
- `neutral ai_reviewer comment before` — any prior reviewer turn matching neither.

So "named the debt" vs "raised concern" is decided purely by which regex family the earlier message hits
(debt-phrase/error ⇒ named; question/suggestion language ⇒ concern), with *named* taking priority when a
turn matches both.

**PR-author signal** (`persist_spotter`): the PR's author login is classified (human vs agent bot). A
**human-opened PR ⇒ dev_elicited** even with no in-thread signal, because the developer initiated/owns
the work. This is what reconciles AIDev with the review-driven Suzhen cohort (AIDev's developer prompt
lives in *who opened the PR*, not an in-thread comment).

> **dev_elicited (a) in-thread:** `oven-sh/bun#24136` circumstance `dev alluded to error` on
> `"XXX: 'fn (*FetchTasklet) error{}!void' coerces to …"`.
> **dev_elicited (b) human-opened PR:** `stacklok/toolhive#2060` (author `JAORMX`, a human) →
> `"Basic and Digest authentication are not implemented"` — no one pointed at this debt, but a developer
> opened/directed the PR, so it is dev-initiated, not spontaneous.
> **spontaneous:** `fsprojects/FSharp.Data#1570` (bot-opened) `"Should throw \"Not implemented\" exception"` —
> agent-bot PR, no dev signal.
> **reviewbot_elicited:** `onetimesecret/onetimesecret#2053` circumstance `ai_reviewer raised concern` →
> `"For now, return the key as a placeholder"`.

*Caveat (documented):* author-login classification treats any login containing an agent name as an
agent — e.g. a composite human handle like `ChrisRackauckas-Claude` is read as `claude`, so its PR is
*not* counted human-opened; and an automation-opened PR (`github-actions[bot]`) is neither human nor
agent → defaults to spontaneous. These are edge cases at the margin of the spontaneous/dev split.

---

## 4. Resolution detection

### 4a. Code-comment SATD — physical removal via git (`stage5_aidev.py`)
We anonymously clone each repo's default branch and trace the comment:
- present at HEAD (exact **or** whitespace-normalized, so squash/Prettier reflow doesn't fool it) →
  **alive** (not resolved);
- gone from HEAD → `git log -S` **pickaxe** since `merged_at` finds the removing commit →
  **resolved** (or `comment_deleted_only` if only the comment line changed, `file_deleted` if the file
  was deleted);
- removed before/at merge (introducer not an ancestor of HEAD) → **self_resolved_premerge**;
- merged, gone, but unpinnable on the default branch → folded to resolved as `code_removal_unpinned`
  (the comment provably existed and is gone; only a reverted introducing commit wouldn't count).

> Example: `tphakala/birdnet-go#836` `"For now, process individually"` → `code_removal`, removing commit
> `97fad99a4fd1`.

### 4b. Conversation SATD — two instance-tied pathways (`stage6_resolution_outcome.py`)
- **immediate_fix** — a same-file AI removal-confirmation (`is_debt_removal`: first-person/action verb +
  removal verb governing a debt term in the same clause), anchored to the latest commit at/<= the
  confirmation.
- **later_confirmation** — a later agent *or* developer turn that confirms *this* debt was addressed
  (removal language + the same debt keyword).
- otherwise **dropped** (abandoned PR) or **excluded** (merged, no observable signal — kept for RQ1
  noticing, out of the RQ2 denominator).

> Example: `nearai/ironclaw#1574` `later_confirmation` (resolver human):
> `"Follow-up on the review notes: fixed in … Renamed the local binding …"`.

### 4c. Issue channel — closed by a merged PR (`stage3d/3e/3f`, `stage6`)
See §5. A SATD deferred to an issue is resolved when that issue is **closed by a merged PR**.

> Example: `pdpipe/pdpipe#147` → `issue_closed`, tracked issue `pdpipe/pdpipe#148`.

**Outcome** per instance (`stage6`): `resolved` / `dropped` (abandoned-PR or merged-but-still-present)
/ `excluded` (unobservable). Reported resolution rate = resolved / (resolved + dropped).

---

## 5. Issue channel — detecting issues that track SATD (`stage3d`, `stage3e`, `stage3f`)

**Only an explicit numeric cross-reference counts — never bare word-matching.** `stage3d` scans each
SATD-bearing PR's body + conversation for issue references in three forms:
- same-repo `#N` (`(?:^|[\s(\[])#(\d+)\b`),
- cross-repo `owner/repo#N` (`\b([\w.-]+/[\w.-]+)#(\d+)\b`),
- issue URL (`github\.com/owner/repo/issues/(\d+)`).

A reference is kept **only if the text unit also fires a SATD signal** (`detect_conversational` or
`_DEBT_TERM`) — i.e. the issue is referenced *because of* debt, not a coincidental "#1". Intent is
tagged from the verb near the reference: **deferral** (`open/creat/file/raise/track/defer/split/
follow-up/see/capture`), **resolution** (`close/fix/resolve/address`), or **mention**.

`stage3e` (GitHub API) then fetches each issue: filters out numbers that are actually PRs
(`pull_request` field), records `state`/`closed_at`, and flags whether the issue body itself reads as
debt (`issue_debt`). **Validity**: (b) PR→issue with deferral/create intent, **or** (a) the issue body
references the PR back *and* the issue reads as debt. `stage3f` confirms the issue was **closed by a
merged PR** (not stale/wontfix) and counts AI commits to the fixing PR before close.

> Examples (`aidev_issue_links`, valid links):
> - `gastownhall/beads#2243 → beads#2249` intent `deferral`, `issue_debt=1`, `closed_by_merge=1` —
>   context: *"…detection is tracked separately in follow-up issue: #2249. Net effect: CI is now green…"*
> - `pdpipe/pdpipe#147 → #148` intent `deferral`, `closed_by_merge=1` — *"…re-raise paths. Follow-up
>   (low priority): #148"*.
> The keyword cues that surface these: **follow-up**, **tracked separately in**, **tracking**,
> **cross-link umbrella issue**, next to a numeric `#N`.

---

## 6. Revisits to resolution — `stage5c` (patch-based), used by `stage6`/graphs

`n_revisits_pre_res` / `n_ai_near_edits` = **method-level near-edits around the SATD before it was
resolved**, computed from the diffs (git hunk `@@ … @@` headings, ±40-line fallback). This is
**authorship-independent** on purpose: AIDev attributes commits to the human/web-flow merger, so counting
"agent-authored commits" is unreliable (only ~3.5k agent-login commits across the cohort). Near-edits
are not affected by that.

---

## 7. Caveats / limitations (state these when asked "how reliable is X")

- **AIDev stores no commit dates** (the `author`/`committer` parquet fields are login strings only). We
  recover them from GitHub (`aidev_commit_dates`, ~97% of SATD-PR commits); ~3% (deleted/DMCA'd PRs) stay
  undated and default to spontaneous on the code side.
- **AIDev PRs are agent-authored but human-owned** (`author_type='ai'`, 60% of SATD-PRs have a human
  `author` login). The developer's *initiating* prompt usually lives in the originating issue/assignment,
  which AIDev does not store — we proxy it with the human-opened-PR signal (§3).
- **Conversation debt is often unobservable for resolution** (no code artifact) → `excluded` from RQ2
  rather than miscounted as unresolved.
- **Spontaneous vs dev-elicited is a definitional choice** (debt-specific vs work-initiated); we use
  work-initiated (a dev-created PR ⇒ not spontaneous), which aligns AIDev (~34–66% dev depending on agent)
  with the review-driven Suzhen cohort.

---

## 8. Reproducing the examples

All examples above are regenerated by `python3 gather_methodology_examples.py` (reads `db/satd_mining.db`).
The classification columns are produced by `persist_spotter.py` (spotter/how_spotted) and
`stage6_resolution_outcome.py` (outcome/resolved_how/n_revisits_resolve); the per-agent RQ figures by
`graph_rq_hierarchy.py`.

---

## 9. Debt TYPE (design / bug / refactoring) — `satd_detect.classify_type`

Each code-comment SATD is assigned one type, first match wins:
- **bug** — text matches `_DEFECT` (`\bbug\b`, `broken`, `crash`, `wrong`, `incorrect`, `doesn't work`,
  `race condition`, `deadlock`, `memory leak`, `null pointer`/`npe`, `out of bounds/memory/range`,
  `overflow`/`underflow`, `off-by-one`) **and not** `_HANDLE_ERROR` (`handle error` / `error handling`,
  excluded so routine error-handling TODOs aren't miscounted as bugs).
- **refactoring** — matches `_REFACTOR` (`refactor`, `restructure`, `reorganize`, `clean up`,
  `simplify`, `consolidate`, `decouple`, `modularize`, `extract`, `move to/into`, `split`, `duplicat*`,
  `copy-paste`, `DRY`, `dead code`).
- **design** — everything else (the catch-all; it's the renamed old "FEATURE" bucket, so most
  requirement/design debt lands here). Counts (code SATD, all cohorts): **design 10,261 / refactoring 341 / bug 250**.

Examples:
- **design:** `Azure/azure-sdk-for-net#52699` — *"temporary default to Tenant, will be properly set later…"*
- **bug:** `Azure/azure-sdk-for-net#51116` — *"TODO -- this is incorrect, but we put it here as a placeholder."* (`incorrect` → `_DEFECT`)
- **refactoring:** `microsoft/onnxruntime#22676` — *"…not supported to pick the last index in case of encountering **duplicate** max values."* (matched `_REFACTOR` on `duplicat*` — note: keyword matching is literal, so "duplicate values" can match the code-duplication cue; a known precision limit of the type classifier.)

## 10. Concern / sub-concern — `graph_admissions` sub-classifier (keyword, first match wins)

Within design debt, the sub-concern is assigned by keyword (`_h(text, …)`):
- **interim / stopgap:** `for now`, `temporar*`, `temp `, `quick fix`, `workaround`/`work around`, `for the time being`, `hack`
- **placeholder / stub:** `placeholder`, `stub`, `dummy`
- **hardcoded / magic value:** `hardcod`, `hard-cod`, `magic number`, `magic value`
- **unsupported / not implemented:** `not supported`, `not yet`, `nyi`, `not implemented`, `only support`, `doesn't/does not support`, `unsupported`, `no support`, `not available`
- **deferred removal / revisit:** `remove this`, `should be removed`, `get rid`, `eventually`, `revisit`, `once `, `when we`

Examples:
- interim/stopgap: `azure-sdk-for-net#52699` — *"temporary default to Tenant, will be properly set later…"*
- placeholder/stub: `azure-sdk-for-net#52699` — *"This is a placeholder for the actual implementation"*
- unsupported/not-implemented: `azure-sdk-for-net#52699` — *"…the Image URI is not supported, uncomment … when ICM 686545924 is resolved."*
- hardcoded/magic value: `dotnet/runtime#75929` — *"for now is hardcode a list of known supported types…"*
- deferred removal/revisit: `cosmos/cosmos-sdk#25705` — *"TODO(@julienrbrt): Eventually remove this SDK dependency"*

## 11. Documentation graphs (`adm_agent_doc_overall` / `_ownself`)

These are kept **consistent with the RQ elicitation figure** (Table 2 / `rq_elicitation_by_agent`): same
cohort (AIDev, where `how_spotted` is persisted), same unit (agent-spotted SATD **instances**), same N.
- **`adm_agent_doc_overall`** = count of the agent's **agent-spotted** SATD (`spotter='agent'`), which by
  construction equals **spontaneous + dev-elicited + reviewbot-elicited**. So Claude = 8,551, codex 8,927,
  cursor 2,734, devin 3,478, jules 1,481 — identical to Table 2.
- **`adm_agent_doc_ownself`** = the **spontaneous** subset (`how_spotted='spontaneous'`): Claude 302
  (4% of agent-spotted), codex 14, cursor 121, devin 2,627 (76%), jules 1,180 (80%).
- **Scope note:** Copilot is **not** shown in these two figures — it's Suzhen-only and `how_spotted`
  is persisted for AIDev only. (Copilot still appears in the combined-cohort documentation-*channel* and
  debt-*type* figures, §5/§6 of the tables, which don't depend on `how_spotted`.)
- Earlier versions divided "PRs documenting debt" by **all** of the agent's PRs (e.g. Claude 4,199/172,372
  = 2.4%) and used the old `circumstance='SPONTANEOUS'` label — that inflated the spontaneous count
  (Claude ≈2,700) and used a 172k denominator inconsistent with the ~25k agent-spotted debt actually
  classified. Both are now replaced by the agent-spotted / `how_spotted` basis above.

## 12. Survival (`adm_survival_by_agent`)

- **Clock start:** the PR's `merged_at` — when the debt landed on the mainline (unmerged PRs are excluded;
  the debt never entered the codebase).
- **Event (uncensored):** the SATD comment is removed on the default branch (`resolved` /
  `file_deleted` / `comment_deleted_only`) → survival = `resolved_at − merged_at`; `self_resolved_premerge`
  ≈ 0 (removed at merge).
- **Right-censored:** the comment is still present at the repo's **HEAD snapshot** (`status='alive'`) →
  survival = HEAD-snapshot-date − `merged_at` (the debt has survived *at least* this long).
- **Why Devin's curve drops sharply near ~280 days:** it's a **temporal batch/censoring artifact, not a
  behaviour**. Devin's median survival is 280 d, and 240–360 d holds a large block — **~229 removal
  events** (self_resolved_premerge 173 + file_deleted 45 + resolved 8 + comment_deleted 3) plus **184
  censored (alive)** instances. Devin's PRs cluster in a calendar window, and the fixed HEAD-snapshot
  date puts both their removals and their censoring around the same ~280-day mark, so the KM curve has a
  cliff there. It does **not** mean Devin "waits 280 days" to fix debt. *(The premerge cluster at 240–360 d
  is worth a closer look if exact attribution is needed.)*

## 13. Near-edit revisit — worked example

`n_ai_near_edits` / `n_revisits_pre_res` = method-level edits around the SATD line before it was resolved
(from diff hunk `@@ … @@` headings, ±40-line fallback; authorship-independent — see §6).

> **`safe-global/safe-wallet-monorepo#7483`** — code comment *"Placeholder — will filter checks to only
> changed files in a future task."* → `n_ai_near_edits=3`, `n_revisits_pre_res=3`, `outcome=resolved`.
> Reading: the method containing this placeholder was edited **3 times** after it was introduced and
> before the placeholder was finally removed — i.e. the debt was revisited 3× on the way to resolution.
