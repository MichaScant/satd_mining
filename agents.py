"""Actor classification: genuine code-GENERATING agents vs automation bots vs humans.

Stage 1 originally counted *any* bot as "AI", so dependency/CI/release automation
(renovate, dependabot, github-actions, …) polluted the AI cohort even though their PRs'
debt is human-written. This module is the single source of truth that separates:

  coding_agent    — writes code: Copilot, Devin, Claude, Cursor, Codex, Aider, OpenHands, …
  automation_bot  — manages the repo, doesn't author feature code: renovate, dependabot, CI, …
  human           — a person

Used by Stage 1 (cohort = coding agents only), Stage 3 (stamp the introducer), and
Stage 5 (classify who revisited / resolved). So we never have to filter automation out
after the fact again.
"""
import re

# name -> pattern for code-generating agents (first match wins for agent_name)
CODING_AGENTS = [
    ("copilot",       r"copilot"),
    ("devin",         r"devin"),
    ("claude",        r"claude|anthropic"),
    ("cursor",        r"cursor"),
    ("codex",         r"codex"),
    ("aider",         r"aider"),
    ("openhands",     r"openhands|opendevin"),
    ("sweep",         r"\bsweep\b|sweep-ai"),
    ("sourcegraph",   r"sourcegraph|\bcody\b"),
    ("gpt-engineer",  r"gpt-?engineer"),
    ("codegen",       r"\bcodegen\b"),
]
_AGENT_RES = [(n, re.compile(p, re.I)) for n, p in CODING_AGENTS]
CODING_AGENT_RE = re.compile("|".join(p for _, p in CODING_AGENTS), re.I)

# automation that opens/manages PRs but does not author feature code (bot-specific names
# to avoid false positives on ordinary words like "release")
AUTOMATION_RE = re.compile(
    r"renovate|dependabot|github-actions|release-please|release-drafter|semantic-release|"
    r"\bbackport\b|lingohub|weblate|crowdin|transifex|github-policy|octo-sts|datadog|"
    r"coderabbit|\bsnyk\b|greenkeeper|mergify|auto-pr|\bbors\b|netlify|vercel\[bot\]|"
    r"codecov|allcontributors|imgbot|pre-commit-ci|whitesource|deepsource|sonarcloud|"
    r"restyled|stale\[bot\]|\[bot\]", re.I)


def agent_name(text):
    for n, rx in _AGENT_RES:
        if rx.search(text or ""):
            return n
    return None


def classify_actor(*identities):
    """(kind, agent_name) where kind in coding_agent | automation_bot | human.

    Pass any mix of identity strings (PR-author login/type, git author, committer,
    co-author trailers). A coding agent anywhere wins over automation."""
    blob = " ".join(str(x) for x in identities if x)
    if CODING_AGENT_RE.search(blob):
        return "coding_agent", agent_name(blob)
    if AUTOMATION_RE.search(blob):
        return "automation_bot", None
    return "human", None


def committed_by(author_login, committer_login):
    """Who put the commit into the repo: agent | github_merge | human.

    'github_merge' (committer == 'web-flow') and 'human' are the "AI generated it,
    a human merged it" cases."""
    cm = str(committer_login or "")
    if CODING_AGENT_RE.search(cm):
        return "agent"
    if cm == "web-flow":
        return "github_merge"
    if cm:
        return "human"
    return "agent" if CODING_AGENT_RE.search(str(author_login or "")) else "human"
