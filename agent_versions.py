"""Default-model-by-date inference for coding agents.

We cannot recover the EXACT model that authored a given PR: it is not in the AIDev schema, not in
git/commit metadata, not in the committed diffs (we checked — the only 'model' strings in the diffs
are the target repos' own LLM code), and only ~3% of PRs disclose it in free text. So instead we
assign each PR the model that was the agent's *documented default (general-availability) model* on
the PR's date. This is an APPROXIMATION and is disclosed as such (see DISCLOSURE below).

It is honest only where a vendor publishes a single default:
  - Claude Code, OpenAI Codex, Google Jules  -> documented default timeline, encoded below.
  - Cursor   -> the agent is user-selected / "Auto"-routed across vendors; no single default.
  - Devin    -> Cognition does not disclose the base model at any point.
For the last two we return an explicit "undisclosed ..." marker rather than inventing a version.

Dates are ISO-8601 strings ('2025-09-20T..'); we compare the first 10 chars (YYYY-MM-DD), which
sorts lexically, so no datetime parsing is needed. infer_default_version(agent, date) returns the
default model active on `date`, or None if the agent has no documented default or the date is
unknown/outside coverage.

Sources (release / default-switch dates):
  Claude Code : research preview w/ 3.7 Sonnet Feb 2025; GA w/ Sonnet 4 on 2025-05-22;
                Sonnet 4.5 became default sonnet 2025-09-29 (stayed default through the AIDev
                window, which ends 2026-03). Opus was the premium tier throughout — see note.
  OpenAI Codex: codex-1 (o3-tuned) 2025-05-16; GPT-5-Codex 2025-09-15; GPT-5.1-Codex-Max 2025-11-19;
                GPT-5.2-Codex 2026-01-14; GPT-5.3-Codex 2026-02-05.
  Google Jules: Gemini 2.5 Pro from public beta 2025-05-20; Gemini 3 Pro from 2025-11-18.
"""

# agent_name -> [(effective_from 'YYYY-MM-DD', default_model), ...] ascending by date.
# The model in effect on a date is the LAST entry whose date is <= that date.
VERSION_TIMELINE = {
    "claude": [
        ("2025-02-24", "claude 3.7 sonnet"),   # Claude Code research preview
        ("2025-05-22", "claude sonnet 4"),      # Claude Code GA default
        ("2025-09-29", "claude sonnet 4.5"),    # default sonnet through the AIDev window
    ],
    "codex": [
        ("2025-05-16", "codex-1 (o3)"),         # Codex cloud agent launch
        ("2025-09-15", "gpt-5-codex"),
        ("2025-11-19", "gpt-5.1-codex-max"),
        ("2026-01-14", "gpt-5.2-codex"),
        ("2026-02-05", "gpt-5.3-codex"),
    ],
    "jules": [
        ("2025-05-20", "gemini 2.5 pro"),       # public beta default (free tier)
        ("2025-11-18", "gemini 3 pro"),         # Gemini 3 launch
    ],
}

# Agents with NO single documented default — never invent a version for these.
UNDISCLOSED = {
    "cursor": "undisclosed (user-selected / Auto-routed)",
    "devin":  "undisclosed (proprietary — Cognition does not disclose the base model)",
    "copilot": "undisclosed (user-selected across GPT/Claude/Gemini)",
}


def _day(date):
    """First 10 chars of an ISO date ('2025-09-20T..' -> '2025-09-20'), or None."""
    if not date:
        return None
    s = str(date).strip()
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else None


def infer_default_version(agent, date):
    """Documented default model for `agent` on `date` (ISO string), or None.

    Returns the UNDISCLOSED marker for cursor/devin/copilot, and None when the agent has no
    timeline, the date is missing/malformed, or the date precedes the agent's first release."""
    if not agent:
        return None
    a = str(agent).strip().lower()
    if a in UNDISCLOSED:
        return UNDISCLOSED[a]
    tl = VERSION_TIMELINE.get(a)
    if not tl:
        return None
    d = _day(date)
    if not d:
        return None
    out = None
    for start, model in tl:          # ascending; take the last start <= d
        if start <= d:
            out = model
        else:
            break
    return out                        # None if d precedes the agent's first release


# Ready-to-paste methodology/limitations paragraph for the write-up.
DISCLOSURE = (
    "Model-version attribution. The exact model that authored each pull request is not recoverable "
    "at scale: it is absent from the AIDev schema, from git and GitHub commit/PR metadata, and from "
    "the committed diffs, and is only sporadically disclosed in free text. We therefore attribute to "
    "each PR the agent's documented default (general-availability) model as of the PR's creation "
    "date, using published release timelines (Claude Code: 3.7 Sonnet -> Sonnet 4 [2025-05-22] -> "
    "Sonnet 4.5 [2025-09-29]; OpenAI Codex: codex-1/o3 -> GPT-5-Codex [2025-09-15] -> GPT-5.1-Codex-"
    "Max [2025-11-19] -> GPT-5.2-Codex [2026-01-14] -> GPT-5.3-Codex [2026-02-05]; Google Jules: "
    "Gemini 2.5 Pro -> Gemini 3 Pro [2025-11-18]). This is an approximation: it ignores user overrides "
    "and premium tiers (e.g. Claude Code users who select Opus), and is applied only where a vendor "
    "publishes a single default. For Cursor (user-selected / 'Auto'-routed) and Devin (undisclosed "
    "proprietary base model), no default is documented, so their PRs are reported as model-agnostic "
    "rather than assigned an inferred version."
)


if __name__ == "__main__":  # quick self-check
    checks = [
        ("claude", "2025-03-01", "claude 3.7 sonnet"),
        ("claude", "2025-06-01", "claude sonnet 4"),
        ("claude", "2026-02-15", "claude sonnet 4.5"),
        ("codex",  "2025-06-01", "codex-1 (o3)"),
        ("codex",  "2025-10-01", "gpt-5-codex"),
        ("codex",  "2026-01-20", "gpt-5.2-codex"),
        ("codex",  "2026-03-15", "gpt-5.3-codex"),
        ("jules",  "2025-07-01", "gemini 2.5 pro"),
        ("jules",  "2026-01-01", "gemini 3 pro"),
        ("cursor", "2026-01-01", UNDISCLOSED["cursor"]),
        ("devin",  "2026-01-01", UNDISCLOSED["devin"]),
        ("claude", "2024-01-01", None),           # before first release
        ("claude", None,         None),           # missing date
    ]
    ok = True
    for agent, date, want in checks:
        got = infer_default_version(agent, date)
        flag = "OK " if got == want else "XX "
        if got != want:
            ok = False
        print(f"  {flag}{agent:7} {str(date):12} -> {got!r}  (want {want!r})")
    print("ALL PASS" if ok else "FAILURES ABOVE")
