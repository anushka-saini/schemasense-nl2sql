# runs every gold (and alt) query in eval/test_set.json
# - stores row_count and expected rows back into the file
# - prints gold queries that error or return nothing, so broken ones show up
# - warns if a test question looks like one of the v3 worked examples
# output also saved to logs/gold_check.txt
# run: python eval/check_gold.py

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config
from run_sql import run
from score import norm, same_result

TEST_SET = ROOT / "eval" / "test_set.json"
MAX_STORED = 50  # store expected rows only if there are this many or fewer


def words(s):
    return set(re.findall(r"[a-z0-9]+", s.lower())) - {"the", "a", "of", "in", "for", "what", "how", "is", "are", "and", "which", "many", "was"}


def v3_questions():
    text = (config.PROMPTS / "v3.txt").read_text(encoding="utf-8")
    ex = text.split("Examples", 1)[1]
    return re.findall(r"^Question: (.+)$", ex, re.M)[:-1]  # last one is the {question} slot


def main():
    items = json.loads(TEST_SET.read_text(encoding="utf-8"))
    lines = [f"gold check run at {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    problems = []

    for it in items:
        if it["gold_sql"] is None:
            it["expected"], it["row_count"] = "REFUSE", None
            lines.append(f"{it['id']}  REFUSE")
            continue
        r = run(it["gold_sql"], limit=None, timeout=60)
        if not r["ok"]:
            problems.append(f"{it['id']} gold ERROR: {r['error']}")
            it["expected"], it["row_count"] = None, None
            lines.append(f"{it['id']}  ERROR {r['error']}")
            continue
        df = r["df"]
        rows = [[norm(v) for v in row] for row in df.itertuples(index=False, name=None)]
        it["row_count"] = len(rows)
        it["expected"] = rows if len(rows) <= MAX_STORED else f"{len(rows)} rows (too many to store)"
        if not rows:
            problems.append(f"{it['id']} gold returns 0 rows")
        elif all(v is None for row in rows for v in row):
            problems.append(f"{it['id']} gold returns only NULL")
        shown = rows if len(rows) <= 5 else rows[:5] + [["..."]]
        lines.append(f"{it['id']}  {len(rows)} row(s)  {shown}")

        for i, alt in enumerate(it.get("alt_sql", [])):
            a = run(alt, limit=None, timeout=60)
            if not a["ok"]:
                problems.append(f"{it['id']} alt{i + 1} ERROR: {a['error']}")
            elif len(a["df"]) == 0:
                problems.append(f"{it['id']} alt{i + 1} returns 0 rows")
            else:
                same = same_result(a["df"], df)
                ar = [[norm(v) for v in row] for row in a["df"].head(5).itertuples(index=False, name=None)]
                lines.append(f"       alt{i + 1}: {len(a['df'])} row(s) {ar}{'  (same as gold)' if same else ''}")

    TEST_SET.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    # v3 examples must not leak into the test set
    lines += ["", "== overlap with v3 worked examples (word overlap >= 0.5) =="]
    hits = 0
    for ex in v3_questions():
        for it in items:
            a, b = words(ex), words(it["question"])
            j = len(a & b) / max(1, len(a | b))
            if j >= 0.5:
                hits += 1
                lines.append(f"WARNING {it['id']} ({j:.2f}) '{it['question']}'  ~  example '{ex}'")
    if not hits:
        lines.append("none found")

    c = Counter((it["tier"], it["split"]) for it in items)
    lines += ["", "== split =="]
    for t in dict.fromkeys(it["tier"] for it in items):
        lines.append(f"{t:12s} dev {c[(t, 'dev')]:2d}   test {c[(t, 'test')]:2d}")
    lines.append(f"{'total':12s} dev {sum(v for (t, s), v in c.items() if s == 'dev'):2d}   "
                 f"test {sum(v for (t, s), v in c.items() if s == 'test'):2d}")
    lines.append(f"verified: {sum(it['verified'] for it in items)} of {len(items)}")

    lines += ["", "== gold queries that error or return nothing =="]
    lines += problems or ["none"]
    out = "\n".join(lines)
    print(out)
    (config.LOGS / "gold_check.txt").write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
