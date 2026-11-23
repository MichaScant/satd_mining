"""AI code-review TOOL identification + maturity-phase by date.

We cannot recover the LLM a review bot used (proprietary, configurable, rotating — e.g. CodeRabbit
runs o3 / o4-mini / GPT-4.1 and swaps them). What IS recoverable: (a) the review TOOL, reliably,
from the bot login, and (b) the tool's maturity PHASE at the review's date, from public release
timelines. These are continuously-deployed SaaS, so a phase (preview / GA / feature-era) is the
truthful analogue of "which version was active" — there is no discrete per-review version string.

Use reviewer_tool(actor) for the tool, and reviewer_phase(tool, date) for the phase on a date.
"""
import re

# login pattern -> (tool, vendor). First match wins; ordered specific-first.
_TOOLS = [
    (r"coderabbit",                       ("CodeRabbit",            "CodeRabbit")),
    (r"copilot-pull-request-reviewer|copilot-pr-review", ("Copilot code review", "GitHub/OpenAI")),
    (r"gemini-code-assist",               ("Gemini Code Assist",    "Google")),
    (r"greptile",                         ("Greptile",              "Greptile")),
    (r"sourcery",                         ("Sourcery",              "Sourcery")),
    (r"qodo|qodana|codium",               ("Qodo (Codium)",         "Qodo")),
    (r"cubic-dev-ai",                     ("Cubic",                 "Cubic")),
    (r"openclaw-barnacle",                ("OpenClaw",              "OpenClaw")),
    (r"bito",                             ("Bito",                  "Bito")),
    (r"ellipsis-dev",                     ("Ellipsis",              "Ellipsis")),
    (r"korbit",                           ("Korbit",                "Korbit")),
    (r"entelligence",                     ("Entelligence",          "Entelligence")),
    (r"codescene",                        ("CodeScene",             "CodeScene")),
    (r"pr-agent|codeant",                 ("PR-Agent/CodeAnt",      "misc")),
    (r"\bcopilot\b|^copilot$",            ("Copilot code review",   "GitHub/OpenAI")),  # bare 'Copilot'
]
_TOOLS_RE = [(re.compile(p, re.I), v) for p, v in _TOOLS]

# maturity phase by date, where a public timeline exists (effective_from ascending).
# Dates are published release/GA milestones; sources in the paper's reference list.
_PHASES = {
    "Copilot code review": [            # GitHub changelog
        ("2025-02-26", "public preview"),
        ("2025-04-04", "GA"),
        ("2025-05-28", "GA (expanded coverage)"),
    ],
    "CodeRabbit": [                     # continuous SaaS; coarse era markers only
        ("2023-01-01", "GA (continuous SaaS; backend rotates — GPT-4.1/o3/o4-mini-class)"),
        ("2025-05-01", "GA (+IDE/VS Code era)"),
    ],
    "Gemini Code Assist": [
        ("2024-01-01", "GA (continuous SaaS; Gemini backend)"),
    ],
}


def reviewer_tool(actor):
    """(tool, vendor) for a review-bot login, or ('other_ai_reviewer', 'unknown')."""
    s = str(actor or "")
    for rx, v in _TOOLS_RE:
        if rx.search(s):
            return v
    return ("other_ai_reviewer", "unknown")


def reviewer_phase(tool, date):
    """Maturity phase of `tool` on ISO `date` (YYYY-MM-DD...), or None.
    'not yet available' if the date precedes the tool's first known milestone."""
    tl = _PHASES.get(tool)
    if not tl or not date:
        return None
    d = str(date)[:10]
    if len(d) < 10 or d[4] != "-":
        return None
    out = "not yet available"
    for start, phase in tl:
        if start <= d:
            out = phase
        else:
            break
    return out


DISCLOSURE = (
    "AI-reviewer attribution. For AI code-review bots we identify the TOOL (CodeRabbit, Copilot "
    "code review, Gemini Code Assist, Greptile, Sourcery, Qodo, …) reliably from the bot login, and "
    "the tool's maturity PHASE at the review date from public release timelines (e.g. Copilot code "
    "review: public preview 2025-02-26 -> GA 2025-04-04). These are continuously-deployed SaaS "
    "services: they do not stamp a version on each review and their backend LLM is proprietary, "
    "repo-configurable, and rotating (CodeRabbit, for instance, runs o3/o4-mini/GPT-4.1 and swaps "
    "them). We therefore report tool + maturity phase, not a model version — the latter is not "
    "recoverable at scale."
)


if __name__ == "__main__":  # smoke check
    for a in ("coderabbitai[bot]", "copilot-pull-request-reviewer[bot]", "Copilot",
              "gemini-code-assist[bot]", "greptile-apps[bot]", "sourcery-ai[bot]", "qodo-code-review[bot]"):
        t, v = reviewer_tool(a)
        print(f"  {a:38} -> {t:22} ({v})  phase@2025-03-10={reviewer_phase(t, '2025-03-10')!r}")
