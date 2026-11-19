"""Characterize the SATD instances whose agent can't be resolved from the PR, so we know what the
93K 'None' bucket actually is and whether commit-level info can attribute them. Read-only."""
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents

c = sqlite3.connect(str(Path(__file__).resolve().parent / "db" / "satd_mining.db"))


def cols(t):
    return [r[1] for r in c.execute(f"PRAGMA table_info({t})")]


print("=== table sizes ===")
for t in ("satd_instances", "prs", "commits"):
    print(f"  {t:16}", c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
print("commits columns:", cols("commits"))
print("satd_instances columns:", cols("satd_instances"))

# resolve the same way the backfill does: instance agent_name, else prs.agent, else classify author
pr_meta = {}   # (repo,pr) -> (agent_or_None_after_author_classify, cohort)
pcols = cols("prs")
acol = "author" if "author" in pcols else None
atcol = "author_type" if "author_type" in pcols else None
sel = ["repo", "pr_number", "agent", "cohort"] + [x for x in (acol, atcol) if x]
for row in c.execute(f"SELECT {', '.join(sel)} FROM prs"):
    r, n, a, cohort = row[0], row[1], row[2], row[3]
    if not a and acol:
        a = agents.classify_actor(row[sel.index(acol)], row[sel.index(atcol)] if atcol else None)[1]
    pr_meta[(r, n)] = (a, cohort)

# commit-level identity for each sha (author/committer login + message), if the table has them
ccols = cols("commits")
cc = {x: (x in ccols) for x in ("sha", "author_login", "committer_login", "message",
                                "author", "committer", "agent_version")}
csel = [x for x in ("sha", "author_login", "committer_login", "author", "committer", "message",
                    "agent_version") if cc.get(x)]
sha_identity = {}
if "sha" in csel:
    for row in c.execute(f"SELECT {', '.join(csel)} FROM commits"):
        d = dict(zip(csel, row))
        sha_identity[d["sha"]] = d

null_by_cohort = Counter()
null_authors = Counter()
recoverable_from_commit = Counter()   # how many None instances a commit identity WOULD attribute
still_null = 0
total_null = 0
icols = cols("satd_instances")
has_inst_agent = "agent_name" in icols
for row in c.execute("SELECT repo, introducing_pr, introducing_sha, "
                     + ("agent_name" if has_inst_agent else "NULL") + " FROM satd_instances"):
    repo, pr, sha, inst_agent = row
    agent, cohort = (inst_agent, None)
    if not agent:
        agent, cohort = pr_meta.get((repo, pr), (None, None))
    if agent:
        continue
    total_null += 1
    null_by_cohort[cohort] += 1
    ident = sha_identity.get(sha)
    if ident:
        blob = " ".join(str(ident.get(k) or "") for k in
                        ("author_login", "committer_login", "author", "committer", "message"))
        nm = agents.classify_actor(blob)[1]
        if nm:
            recoverable_from_commit[nm] += 1
        else:
            still_null += 1
        # sample the author login for inspection
        null_authors[str(ident.get("author_login") or ident.get("author") or "?")] += 1
    else:
        still_null += 1
        null_authors["<no commit row>"] += 1

print(f"\n=== None-agent instances: {total_null} ===")
print("by PR cohort:", dict(null_by_cohort))
print("\nrecoverable via COMMIT identity (classify author/committer/message):",
      dict(recoverable_from_commit.most_common()))
print("still unattributable after commit check:", still_null)
print("\ntop 20 commit-author logins in the None bucket:")
for a, n in null_authors.most_common(20):
    print(f"   {str(a):40} {n}")
c.close()
