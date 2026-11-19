"""Live, read-only monitor for the AIDev stage-5 run. Polls the DB and prints a compact dashboard
(progress bar, throughput, ETA, status breakdown, merged-PR resolution rate). Opens the DB
read-only so it can NEVER lock the tracker. Ctrl-C to stop.

  python watch_stage5.py [--interval 30]
"""
import argparse
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent / "db" / "satd_mining.db"
BASE = ("FROM satd_instances i JOIN prs p ON p.repo=i.repo AND p.pr_number=i.introducing_pr "
        "WHERE p.cohort='aidev' AND (i.channel IS NULL OR i.channel='code_comment') "
        "AND i.introducing_sha IS NOT NULL")
ORDER = ["alive", "resolved", "comment_deleted_only", "file_deleted",
         "self_resolved_premerge", "untrackable", "pr_unmerged", "pr_open"]


def main(interval):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    total = c.execute(f"SELECT COUNT(*) {BASE}").fetchone()[0]
    if not total:
        print("no AIDev instances match (did stage3_aidev run?)"); return
    last_done = last_t = None
    while True:
        snap = dict(c.execute(f"SELECT i.status, COUNT(*) {BASE} GROUP BY i.status").fetchall())
        done = sum(v for k, v in snap.items() if k is not None)
        pend = total - done
        now = time.time()
        rate = eta = None
        if last_done is not None and now > last_t and done > last_done:
            rate = (done - last_done) / (now - last_t)        # instances/sec
            eta = pend / rate if rate else None
        last_done, last_t = done, now

        merged_tracked = sum(snap.get(k, 0) for k in ("alive", "resolved", "comment_deleted_only", "file_deleted"))
        resolved = sum(snap.get(k, 0) for k in ("resolved", "comment_deleted_only", "file_deleted"))
        n = int(40 * done / total)
        print("\n" + "=" * 56)
        print(f"AIDev stage-5  {done}/{total}  [{'#' * n}{'.' * (40 - n)}] {100 * done / total:.1f}%")
        if rate:
            print(f"  throughput {rate * 60:.0f}/min" + (f"   ETA {eta / 60:.0f} min" if eta else ""))
        for k in ORDER:
            if snap.get(k):
                print(f"   {k:20} {snap[k]}")
        if merged_tracked:
            print(f"  merged-tracked {merged_tracked}:  resolved {resolved} "
                  f"({100 * resolved / merged_tracked:.0f}%)   alive {snap.get('alive', 0)} "
                  f"({100 * snap.get('alive', 0) / merged_tracked:.0f}%)")
        if pend == 0:
            print("  DONE."); break
        time.sleep(interval)
    c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30)
    main(**vars(ap.parse_args()))
