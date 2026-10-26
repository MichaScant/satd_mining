"""Shared SATD detection core — used by Channel A (code comments, Stage 3) and
Channel B (conversation, Stage 3b).

Pattern core = TODO/FIXME/HACK/XXX markers + the Potdar & Shihab (2014) /
Maldonado & Shihab (2015) debt-phrase set (the same set Xiao et al. and Brahmi
used, so our rates are comparable).

PROJECT RULE on markers: a *bare* TODO does NOT count as SATD. TODO is a task
marker that shows up constantly on routine work ("# TODO: bump version"), so it
only counts when it co-occurs with an actual debt phrase. FIXME / HACK / XXX are
debt-indicating on their own and count alone.

Type classification (design | bug | refactoring) ports the previous project's
keyword classifier (classify_with_rminer.py). That project used three buckets
BUG / FEATURE / REFACTORING, where FEATURE was defined as "new functionality OR
changing design". We keep the same signals but name the buckets as the user asked
(design / bug / refactoring): defect phrases -> bug; behaviour-preserving reshape
phrases -> refactoring; design/requirement/other debt -> design (the old FEATURE
catch-all).
"""
import re

# ---------------------------------------------------------------- markers
# FIXME/HACK/XXX are debt on their own; TODO needs a co-occurring debt phrase.
STRONG_MARKER_RE = re.compile(r"\b(FIXME|HACK|XXX)\b", re.I)
TODO_RE = re.compile(r"\bTODO\b", re.I)

# ---------------------------------------------------------------- debt phrases
# Potdar & Shihab / Maldonado & Shihab high-signal phrases (curated subset).
DEBT_PHRASES = [
    r"hack\b", r"hacky", r"kludge", r"kluge",
    r"work[\s-]?around",
    r"\btemp(orary|orarily)?\b", r"for\s+now", r"for\s+the\s+time\s+being",
    r"quick\s+(and\s+dirty|fix)", r"band[\s-]?aid", r"dirty\s+fix",
    r"\bugly\b", r"\bstupid\b", r"\bsilly\b", r"\bnasty\b", r"\bmessy\b", r"\bcrap\b",
    r"not\s+sure", r"don[’']?t\s+(know|understand)", r"no\s+idea",
    r"should\s+(be\s+)?(refactor|fix|rewrit|clean|be\s+removed)",
    r"needs?\s+(to\s+be\s+)?(fixed|refactor|rework|cleaned|revisit)",
    r"needs?\s+work", r"get\s+rid\s+of", r"revisit\s+this", r"come\s+back\s+to",
    r"this\s+is\s+(wrong|broken|a\s+mess|problematic)",
    r"not\s+(yet\s+)?(implemented|supported|done|finished)",
    r"placeholder", r"\bstub\b", r"will\s+break", r"is\s+broken",
    r"\bfixme\b", r"remove\s+this", r"rewrite\s+this", r"giving?\s+up",
]
PHRASE_RE = re.compile("|".join(DEBT_PHRASES), re.I)


def detect(text):
    """Return the matched marker/phrase (str) if `text` admits debt, else None.

    A bare TODO (no debt phrase) returns None by design.
    """
    if not text:
        return None
    m = STRONG_MARKER_RE.search(text)
    if m:
        return m.group(0).lower()
    m = PHRASE_RE.search(text)
    if m:
        return m.group(0).strip().lower()
    return None  # bare TODO (or nothing) -> not SATD


# ---------------------------------------------------------------- type classifier
# ported verbatim-in-spirit from classify_with_rminer.py pattern lists
_DEFECT = re.compile("|".join([
    r"\bbug\b", r"\bbuggy\b", r"\bfix\s+(this|the|a)?\s*(bug|issue)\b", r"\bbroken\b",
    r"\bcrash(es|ed|ing)?\b", r"\bwrong\b", r"\bincorrect\b",
    r"\bdoesn[’']?t\s+work\b", r"\bdoes\s+not\s+work\b", r"\bnot\s+working\b",
    r"\bcorrupt\b", r"\brace\s+condition\b", r"\bdeadlock\b", r"\bmemory\s+leak\b",
    r"\bnull\s*pointer\b", r"\bnpe\b", r"\bout\s+of\s+(bounds|memory|range)\b",
    r"\boverflow\b", r"\bunderflow\b", r"\boff[\s-]?by[\s-]?one\b",
]), re.I)
_HANDLE_ERROR = re.compile(r"\bhandle\s+(the\s+)?error|\berror\s+handling", re.I)
# behaviour-preserving reshaping -> refactoring
_REFACTOR = re.compile("|".join([
    r"\brefactor(ing)?\b", r"\brestructure\b", r"\breorganize\b", r"\bclean\s*up\b",
    r"\bcleanup\b", r"\bsimplify\b", r"\bconsolidate\b", r"\bdecouple\b",
    r"\bmodularize\b", r"\bextract\b", r"\bmove\s+(to|into)\b", r"\bsplit\b",
    r"\bduplicat", r"\bcopy[\s-]?paste", r"\bDRY\b", r"\bdead\s+code\b",
]), re.I)


def classify_type(text):
    """design | bug | refactoring (design is the catch-all, as FEATURE was before)."""
    t = text or ""
    if _DEFECT.search(t) and not _HANDLE_ERROR.search(t):
        return "bug"
    if _REFACTOR.search(t):
        return "refactoring"
    return "design"


# ---------------------------------------------------------------- Channel B helpers
# conversational deferral language (judgment-call extension list, validated separately)
DEFERRAL_PHRASES = [
    r"for\s+now", r"follow[\s-]?up", r"out\s+of\s+scope(\s+for\s+this\s+pr)?",
    r"(will|i['’]?ll)\s+(address|handle|fix|do)\s+(this\s+)?later",
    r"address\s+(this\s+)?(in\s+a\s+)?(later|future|follow)", r"in\s+a\s+(follow|future|later)",
    r"didn[’']?t\s+have\s+time", r"did\s+not\s+have\s+time",
    r"skip(ping|ped)?\b", r"known\s+limitation", r"leave\s+(this|it)\s+for",
    r"revisit\s+later", r"come\s+back\s+to\s+this", r"hard[\s-]?cod(e|ed|ing)",
    r"left\s+.{0,20}\s+as\s+a\s+follow", r"not\s+in\s+(this|scope)",
]
_DEFERRAL_RE = re.compile("|".join(DEFERRAL_PHRASES), re.I)

_FENCED = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`[^`]*`")


def strip_code(text):
    """Remove fenced code blocks and inline spans so quoted `// TODO` in a reply
    (a code comment appearing in conversation) is not counted as conversational SATD."""
    if not text:
        return ""
    return _INLINE.sub(" ", _FENCED.sub(" ", text))


def detect_conversational(text):
    """Channel B: debt phrase/marker OR a conversational-deferral phrase. Code stripped first.
    Returns matched phrase or None. Bare TODO still does not count."""
    clean = strip_code(text)
    hit = detect(clean)
    if hit:
        return hit
    m = _DEFERRAL_RE.search(clean)
    if m:
        return m.group(0).strip().lower()
    return None


# ---------------------------------------------------------------- context-aware precision gate
# No-ML filter that replicates "context" (CASTI's idea) with deterministic rules, to strip the
# keyword false positives (problem-narration, issue-quotes, user docs) from conversational text.
# A span counts as a GENUINE self-admission only if a sentence is: first-person about the author's
# own change, carries a leaving-debt cue, and is NOT describing debt being removed/fixed.
_FIRST_PERSON = re.compile(r"\b(i|i['’]?ve|i['’]?ll|i['’]?m|i['’]?d|we|we['’]?ve|we['’]?ll|my|our)\b"
    r"|\bthis\s+(pr|change|commit|approach|implementation|solution|fix|refactor|code|method|logic|is)\b"
    r"|\bit['’]?s\s+(a|an|still|currently|just|only)\b", re.I)
# instructional / documentation framing (describing a workaround FOR USERS, not admitting debt)
_DOCS_CTX = re.compile(r"\b(how\s+to|demonstrat\w+|example\s+(of|that|demonstrat)|tutorial|"
    r"guide\s+(on|for|to)|shows?\s+(how|users?)|users?\s+can)\b", re.I)
# leaving/deferring debt (introduction cues) — future/modal fixing counts (debt still there)
_INTRO_CUE = re.compile(
    r"\b(for\s+now|for\s+the\s+time\s+being|temporar\w+|follow[-\s]?up|as\s+a\s+follow|in\s+a\s+future|"
    r"should\s+(be\s+)?(refactor\w*|clean\w*|revisit\w*|improv\w*|be\s+removed|be\s+fixed)|"
    r"needs?\s+(to\s+be\b|more\b|further\b|cleanup|work\b|revisit\w*)|"
    r"will\s+(need|fix|address|revisit|refactor|clean|improve)|leav\w*\s+(it|this|them|the)\b|"
    r"keep\w*\s+(it|this)\s+(as|for)\b|hard[-\s]?cod\w*|placeholder|\bstub(bed|s)?\b|work[-\s]?around|"
    r"not\s+(yet\s+)?(implement\w*|support\w*|done|handl\w*)|known\s+limitation|quick\s+(and\s+dirty|fix)|"
    r"band[-\s]?aid|\btodo\b|\bfixme\b|revisit\b|come\s+back\s+to|until\s+(the|we|it|a|this)|"
    r"did(n['’]?t|\s+not)\s+have\s+time|skip(ped|ping)?\b)", re.I)
# COMPLETED removal/fix (debt being taken out, not left) — tense matters: past/perfective
_COMPLETED_FIX = re.compile(r"\b(fixe[ds]|fixed|resolv(ed|es)|remov(ed|es)|eliminat(ed|es)|"
    r"replac(ed|es)|corrected|cleaned\s+up|got\s+rid\s+of|no\s+longer|instead\s+of)\b", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def is_self_admission(text):
    """Context-aware precision gate. Returns the admitting sentence (<=200 chars) or None.
    Deterministic, no ML. Trades some recall for precision (our FPs are a precision problem)."""
    t = strip_code(text)
    # drop markdown-quoted lines (quoting the dev/issue) and checklist/template lines
    kept = [ln for ln in t.splitlines()
            if not re.match(r"\s*>", ln) and not re.match(r"\s*[-*]\s*\[[ xX]\]", ln)]
    t = "\n".join(kept)
    for s in _SENT_SPLIT.split(t):
        s = s.strip()
        if len(s) < 8:
            continue
        if not _FIRST_PERSON.search(s):
            continue
        m = _INTRO_CUE.search(s)
        if not m:
            continue
        if _DOCS_CTX.search(s):
            continue                                      # instructional/user-docs, not an admission
        # polarity: a completed-fix verb right before the cue = removal — UNLESS "with/into/to a"
        # sits between (e.g. "replaced it WITH a hardcoded string" = introducing debt)
        pre = s[max(0, m.start() - 45):m.start()]
        if _COMPLETED_FIX.search(pre) and not re.search(r"\b(with|into|to\s+a)\b", pre):
            continue
        return re.sub(r"\s+", " ", s)[:200]
    return None


# agent REVISITING its own debt: removing/resolving a debt item (not leaving it)
_REMOVE_VERB = re.compile(r"\b(remov\w+|replac\w+|eliminat\w+|delet\w+|got\s+rid\s+of|clean\w*\s+up|"
    r"refactor\w*\s+(out|away)|instead\s+of|no\s+longer|getting\s+rid)\b", re.I)
_DEBT_TERM = re.compile(r"\b(hard[-\s]?cod\w*|work[-\s]?around|hack\w*|\btodo\b|\bfixme\b|placeholder|"
    r"stub\w*|duplicat\w+|dead\s+code|technical\s+debt|tech\s+debt|temporary\s+\w+)\b", re.I)


# agent reporting its own action (implied first-person "I <verb>…")
_ACTION_START = re.compile(r"^\W*(replac\w+|remov\w+|eliminat\w+|delet\w+|fix\w+|updat\w+|clean\w+|"
    r"refactor\w+|chang\w+|moved|extract\w+|consolidat\w+)\b", re.I)


def is_debt_removal(text):
    """Agent describes REMOVING/resolving a debt item (revisiting debt). Returns the sentence or None.
    First-person OR an action-report verb + a removal verb governing a debt term in the same clause."""
    t = strip_code(text)
    kept = [ln for ln in t.splitlines() if not re.match(r"\s*>", ln)]
    t = "\n".join(kept)
    for s in _SENT_SPLIT.split(t):
        s = s.strip()
        if len(s) < 8 or not (_FIRST_PERSON.search(s) or _ACTION_START.search(s)):
            continue
        dm = _DEBT_TERM.search(s)
        rv = _REMOVE_VERB.search(s)
        if dm and rv and abs(rv.start() - dm.start()) <= 60:
            return re.sub(r"\s+", " ", s)[:200]
    return None


def normalize(text):
    """Strip comment markers, collapse whitespace, lowercase — stable tracking identity."""
    t = re.sub(r"^[\s/#*<!>=-]+|[\s*/>=-]+$", "", (text or "").strip())
    return re.sub(r"\s+", " ", t).strip().lower()


# ---------------------------------------------------------------- source-file scoping (Channel A)
# Only scan comments in real source files. Excludes CI YAML, config, docs, lockfiles —
# which is where the casual-TODO false positives (e.g. .github/workflows/*.yaml) came from.
SOURCE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".kts",
    ".go", ".rb", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".hxx", ".cs",
    ".php", ".scala", ".swift", ".m", ".mm", ".sh", ".bash", ".zsh", ".pl", ".pm",
    ".lua", ".r", ".jl", ".dart", ".groovy", ".vue", ".svelte", ".sql", ".ex", ".exs",
    ".erl", ".hs", ".clj", ".cljs", ".cljc", ".fs", ".fsx", ".vb", ".pas", ".d",
    ".nim", ".zig", ".elm", ".ml", ".mli", ".tf", ".proto", ".gradle",
}


def is_source_file(path):
    if not path:
        return False
    p = path.lower()
    import os
    return os.path.splitext(p)[1] in SOURCE_EXTS
