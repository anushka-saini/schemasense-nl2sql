# helps with the error analysis
#   python eval/error_report.py --split test
#       -> logs/error_labels_<split>.csv : one row per failed question in the latest run per version,
#          with a suggested_category (a simple guess from the status / error text) and an EMPTY
#          error_category column for the team to fill in by hand (codes: docs/error_taxonomy.md)
#   python eval/error_report.py --split test --apply
#       -> copies the hand-filled error_category back into the results csvs and prints counts
#          per category and version (for the table in docs/error_taxonomy.md)

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config
from significance import latest

CODES = ["hallucinated_column", "wrong_join_key", "fanout_double_count", "wrong_grain", "missing_status_filter",
         "date_handling", "language_mismatch", "guessed_on_ambiguity", "over_refusal", "answered_unanswerable",
         "format_failure", "wrong_filter_value", "other"]


def suggest(r):
    # only a hint for the person labelling. wrong-result failures need a human to look at the sql
    err = str(r["error"]).lower()
    exp_refuse = str(r["expect_refuse"]).lower() == "true"
    if r["status"] in ("refused", "blocked") and not exp_refuse:
        return "over_refusal"
    if exp_refuse:
        return "answered_unanswerable"
    if "no such column" in err or "unknown table" in err or "no such table" in err:
        return "hallucinated_column"
    if "no sql found" in err:
        return "format_failure"
    if r["tier"] == "ambiguous":
        return "guessed_on_ambiguity"
    if r["tier"] == "fanout":
        return "fanout_double_count?"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["dev", "test"])
    ap.add_argument("--model", default=config.MODEL)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sheet = config.LOGS / f"error_labels_{a.split}.csv"

    files = sorted(config.LOGS.glob("results_*.csv"))
    if not files:
        sys.exit("No results files in logs/. Run eval/run_eval.py first.")

    if not a.apply:
        d = latest(pd.concat(pd.read_csv(f).assign(file=f.name) for f in files), a.split, a.model)
        f = d[d["pass"].astype(str).str.lower() != "true"].copy()
        if f.empty:
            sys.exit("No failed questions in the latest runs.")
        if sheet.exists():
            sys.exit(f"{sheet.name} already exists. Delete it first if you really want a fresh sheet "
                     "(hand labels in it would be lost).")
        f["suggested_category"] = f.apply(suggest, axis=1)
        f["error_category"] = ""
        f["labelled_by"] = ""
        cols = ["file", "run_id", "version", "repeat", "id", "tier", "question", "status", "error",
                "generated_sql", "suggested_category", "error_category", "labelled_by"]
        f[cols].to_csv(sheet, index=False)
        print(f"{len(f)} failed rows written to {sheet}")
        print("Fill error_category (and labelled_by) by hand, then run again with --apply.")
        print("Allowed codes: " + ", ".join(CODES))
        return

    if not sheet.exists():
        sys.exit(f"{sheet.name} not found. Run without --apply first.")
    lab = pd.read_csv(sheet).astype(object).fillna("")
    bad = lab[(lab["error_category"] != "") & ~lab["error_category"].isin(CODES)]
    if len(bad):
        sys.exit("Unknown codes in error_category: " + ", ".join(sorted(bad["error_category"].unique())))
    done = lab[lab["error_category"] != ""]
    for fname, g in done.groupby("file"):
        path = config.LOGS / fname
        res = pd.read_csv(path)
        res["error_category"] = res["error_category"].astype(object).fillna("")
        for _, r in g.iterrows():
            m = (res["id"] == r["id"]) & (res["repeat"] == r["repeat"]) & (res["run_id"] == r["run_id"])
            res.loc[m, "error_category"] = r["error_category"]
        res.to_csv(path, index=False)
    print(f"labelled {len(done)} of {len(lab)} failed rows ({len(lab) - len(done)} still empty)\n")
    if len(done):
        print(pd.crosstab(done["error_category"], done["version"], margins=True, margins_name="total").to_string())


if __name__ == "__main__":
    main()
