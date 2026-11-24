"""Diagnostic: can we date EVERY code-SATD introducing commit straight from AIDev's local parquet?

The analyze stage was defaulting ~74% of code SATD to SPONTANEOUS because their introducing_sha
wasn't dated in the `commits` table. Every commit obviously HAS a date — so this checks whether
AIDev's own pr_commits.parquet (and pr_commit_details.parquet) carries it, which would let us date
100% locally instead of relying on the partial GitHub fetch (aidev_commit_dates).

Prints each parquet's columns, any date-like columns, and how many of our code-SATD introducing_shas
are covered WITH a date in each source. Run on the SERVER (parquet lives there).

  AIDEV_DIR=data/aidev python3 diag_commit_date_coverage.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polars as pl
import config
from db import store


def run():
    d = config.AIDEV_DIR
    conn = store.connect()
    shas = {r[0] for r in conn.execute(
        "SELECT DISTINCT i.introducing_sha FROM satd_instances i "
        "JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND i.channel='code_comment' AND i.introducing_sha IS NOT NULL")}
    print(f"{len(shas)} distinct code-SATD introducing_shas to date\n")

    for fname in ("pr_commits.parquet", "pr_commit_details.parquet"):
        path = d / fname
        if not path.exists():
            print(f"{fname}: MISSING"); continue
        schema = pl.scan_parquet(path).collect_schema()
        print(f"=== {fname} ===")
        print(f"  schema (name: dtype):")
        for name, dt in zip(schema.names(), schema.dtypes()):
            print(f"    {name}: {dt}")
        # the date is almost certainly nested in author/committer -> dump a sample value of each
        for field in ("author", "committer"):
            if field in schema.names():
                sample = (pl.scan_parquet(path).select(field).head(2).collect())
                print(f"  sample {field}: {sample.to_dicts()}")
        print()

    # what the fetched table already covers, for comparison
    fetched = {r[0] for r in conn.execute("SELECT sha FROM aidev_commit_dates "
                                          "WHERE COALESCE(committed_at, authored_at) IS NOT NULL")}
    print(f"aidev_commit_dates (GitHub fetch) covers {len(shas & fetched)} of these shas "
          f"({100*len(shas & fetched)/max(len(shas),1):.0f}%)")
    conn.close()


if __name__ == "__main__":
    run()
