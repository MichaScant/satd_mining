"""Print real examples of coding agents documenting / revisiting their own technical debt,
split by with vs without developer intervention. Writes a readable debt_examples.md too.

  python3 debt_examples.py
"""
import csv
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agents as ag
import satd_detect as sd

BASE = "channel='code_comment' AND introducer_kind='coding_agent'"
SP = "SPONTANEOUS (no dev signal)"
PER = 15   # examples per category

ERR = re.compile(r"\b(doesn'?t work|not working|broken|fail\w*|\berror\b|\bbug\b|\bwrong\b|incorrect|"
                 r"crash\w*|exception|regression|typo)\b", re.I)
GOOD = re.compile(r"\b(for now|temporar|workaround|work around|hack|placeholder|stub|not supported|"
                  r"not implemented|should (be )?(remov|refactor|fix)|revisit|will break|TODO|FIXME|"
                  r"get rid|simplif|quick fix|not ideal|proper|hardcod)\b", re.I)
BADFP = re.compile(r"create (a )?temp|write temp|temp(orary)? (dir|file|table)|--|verbosity|configfile|extract and", re.I)


def norm(t):
    return " ".join((t or "").split())


def good(t):
    t = norm(t)
    return len(t) >= 35 and GOOD.search(t) and not BADFP.search(t)


def has_debt(t):
    return sd.detect_conversational(t) is not None


def dev_before(conn, pid, ts):
    for e in conn.execute("SELECT actor,kind,body,ts FROM review_events WHERE pr_id=? AND body IS NOT NULL "
                          "AND ts IS NOT NULL ORDER BY ts", (pid,)):
        if ag.classify_actor(e["actor"])[0] != "human":
            continue
        t = sd.strip_code(e["body"] or "")
        if e["ts"] <= ts and (e["kind"] == "changes_requested" or has_debt(t) or ERR.search(t)):
            return norm(t)[:180] or e["kind"]
    return None


def run():
    conn = sqlite3.connect(HERE / "db" / "satd_mining.db")
    conn.row_factory = sqlite3.Row
    out = []

    def emit(line=""):
        print(line); out.append(line)

    # 1. DOCUMENT without intervention
    emit("=" * 78)
    emit("1. DOCUMENTING OWN DEBT — WITHOUT developer intervention (spontaneous)")
    emit("=" * 78)
    n = 0
    for r in conn.execute(f"SELECT i.repo repo,i.introducing_pr pr,i.agent_name ag,i.satd_type ty,i.comment_text_raw ct "
                          f"FROM satd_instances i WHERE {BASE} AND i.circumstance=? ORDER BY RANDOM() LIMIT 4000", (SP,)):
        if not good(r["ct"]):
            continue
        emit(f"\n  [{r['ag']}/{r['ty']}] {r['repo']}#{r['pr']}")
        emit(f"     \"{norm(r['ct'])[:160]}\"")
        n += 1
        if n >= PER:
            break

    # 2. DOCUMENT with intervention
    emit("\n" + "=" * 78)
    emit("2. DOCUMENTING OWN DEBT — WITH developer intervention (dev flags it first)")
    emit("=" * 78)
    n = 0
    for r in conn.execute(f"SELECT i.repo repo,cm.pr_id pid,cm.authored_at ts,i.agent_name ag,i.satd_type ty,"
                          f"i.comment_text_raw ct,i.circumstance ci FROM satd_instances i "
                          f"JOIN commits cm ON cm.sha=i.introducing_sha "
                          f"WHERE {BASE} AND i.circumstance IN ('dev named debt','dev alluded to error','changes_requested') "
                          f"ORDER BY RANDOM() LIMIT 4000"):
        if not good(r["ct"]):
            continue
        dev = dev_before(conn, r["pid"], r["ts"])
        if not dev:
            continue
        emit(f"\n  [{r['ag']}/{r['ty']}] {r['repo']}#{r['pid']}  (trigger: {r['ci']})")
        emit(f"     DEV:   \"{dev}\"")
        emit(f"     AGENT: \"{norm(r['ct'])[:160]}\"")
        n += 1
        if n >= PER:
            break

    # 3 + 4. REVISITING from the CSV
    rows = list(csv.DictReader(open(HERE / "agent_notice_revisit.csv")))

    def revisits(trigs, title):
        emit("\n" + "=" * 78)
        emit(title)
        emit("=" * 78)
        n = 0
        for row in rows:
            if row["act"] == "revisit" and row["bucket"] == "dialogue" and row["trigger"] in trigs:
                a = ag.classify_actor(row["agent"])[1]
                tg = f"  ({row['trigger']})" if len(trigs) > 1 else ""
                emit(f"\n  [{a}] {row['repo']}#{row['pr']}{tg}")
                emit(f"     \"{norm(row['text'])[:160]}\"")
                n += 1
                if n >= PER:
                    break

    revisits({SP}, "3. REVISITING OWN DEBT — WITHOUT developer intervention (spontaneous)")
    revisits({"dev named debt", "dev alluded to error", "changes_requested"},
             "4. REVISITING OWN DEBT — WITH developer intervention")

    (HERE / "debt_examples.md").write_text("\n".join(out))
    emit("\n-> saved debt_examples.md")
    conn.close()


if __name__ == "__main__":
    run()
