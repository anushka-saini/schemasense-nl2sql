# makes the human usefulness check sheet: 10 test questions with the model's sql, explanation and result
#   review/usability_sheet.csv   (raters fill this: rater_name, two ratings 1-5, notes)
#   review/usability_sheet.html  (same content, only for reading)
# the 10 questions: 2 per answerable tier (simple, join, aggregation, fanout, ambiguous), picked with a fixed seed,
# answers taken from repeat 1 of the latest main-model test run of --version. no api calls, no ratings filled in.
# an existing csv is never overwritten (it may hold ratings) unless --force.
# run: python eval/make_usability_sheet.py --version v4    then fill the csv, then: python eval/read_usability.py
import argparse
import json
import random
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import config
from make_review_sheets import html_page
from run_sql import run

OUT = ROOT / "review"
TIERS = ["simple", "join", "aggregation", "fanout", "ambiguous"]
SEED = 0
RATE_COLS = ["rater_name", "understandable_1to5", "useful_1to5", "notes"]


def pick_ids():
    items = [it for it in json.loads((ROOT / "eval" / "test_set.json").read_text(encoding="utf-8"))
             if it["split"] == "test"]
    rng = random.Random(SEED)
    ids = []
    for t in TIERS:
        pool = sorted(it["id"] for it in items if it["tier"] == t)
        ids += sorted(rng.sample(pool, 2))
    return ids


def latest_run(version):
    files = sorted(config.LOGS.glob(f"results_{version}_test_*.csv"))
    runs = [pd.read_csv(f) for f in files]
    runs = [d for d in runs if (d["model"] == config.MODEL).all()]
    if not runs:
        sys.exit(f"No held-out test results for {version} with {config.MODEL} in logs/. Run it first.")
    return max(runs, key=lambda d: d["run_id"].iloc[0])


def preview(sql):
    if not isinstance(sql, str) or not sql.strip():
        return ""
    r = run(sql, timeout=60)
    if not r["ok"]:
        return f"ERROR: {r['error']}"
    return r["df"].head(5).to_csv(index=False, lineterminator="\n").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v4")
    ap.add_argument("--force", action="store_true", help="overwrite an existing sheet (its ratings are lost)")
    a = ap.parse_args()
    d = latest_run(a.version)
    run_id = d["run_id"].iloc[0]
    d = d[d["repeat"] == 1].set_index("id")
    rows = []
    for i in pick_ids():
        r = d.loc[i]
        rows.append({"id": i, "tier": r["tier"], "question": r["question"], "version": a.version, "run_id": run_id,
                     "status": r["status"],
                     "generated_sql": "" if pd.isna(r["generated_sql"]) else r["generated_sql"],
                     "explanation": "" if pd.isna(r["explanation"]) else r["explanation"],
                     "result_first_5_rows": preview(r["generated_sql"]),
                     **{c: "" for c in RATE_COLS}})
    df = pd.DataFrame(rows)
    OUT.mkdir(exist_ok=True)
    csv_path = OUT / "usability_sheet.csv"
    if csv_path.exists() and not a.force:
        sys.exit(f"{csv_path.name} already exists, not overwritten (use --force only if it has no ratings).")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    intro = ("Human usefulness check. For each row, read the question, the answer (result rows) and the explanation, "
             "as a non-technical user would. Rate <b>understandable_1to5</b> (1 = cannot follow it, 5 = completely clear) "
             "and <b>useful_1to5</b> (1 = does not help answer the question, 5 = exactly what I needed). One copy of the "
             "rows per rater: fill <b>rater_name</b> on every row you rate. Fill review/usability_sheet.csv, not this page. "
             "Whether the SQL is correct is measured elsewhere; rate what a user would see. "
             f"Answers: {a.version}, run {run_id}, repeat 1.")
    (OUT / "usability_sheet.html").write_text(
        html_page(df, "Usability check (10 test questions)", intro, {"generated_sql", "result_first_5_rows"}),
        encoding="utf-8")
    print(f"{csv_path.name}: {len(df)} rows from {run_id} ({', '.join(df['id'])})")


if __name__ == "__main__":
    main()
