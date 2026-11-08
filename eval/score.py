# scoring for the eval
# execution accuracy: run the generated sql and the gold sql and compare the results
#   - row order is ignored, column names are ignored, floats are rounded to 2 places
#   - extra columns in the generated result are allowed (e.g. a count next to the name)
#     strict_pass also needs the same number of columns
#   - a match with any alt_sql also counts
#   - a correct refusal (CANNOT_ANSWER, or the validator blocking an unsafe request) is a pass
# run: python eval/score.py logs/results_....csv   (prints the summary again)

import sys
from collections import Counter
from itertools import permutations
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from run_sql import run

PRED_LIMIT = 5000  # generated sql is re-run with this limit for scoring
_gold = {}


def norm(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "item"):  # numpy number -> python number
        v = v.item()
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    return str(v).strip()


def rows_of(df):
    return [tuple(norm(v) for v in row) for row in df.itertuples(index=False, name=None)]


def same_result(pred, gold, strict=False):
    # pred / gold are dataframes
    g, p = rows_of(gold), rows_of(pred)
    if len(g) != len(p):
        return False
    k, pk = gold.shape[1], pred.shape[1]
    if pk < k or (strict and pk != k):
        return False
    want = Counter(g)
    # try every way of picking k columns out of pred (column order and names ignored)
    for cols in permutations(range(pk), k):
        if Counter(tuple(r[c] for c in cols) for r in p) == want:
            return True
    return False


def gold_result(sql):
    # gold queries are run once per process and kept (failures are not kept, so they get retried)
    if sql not in _gold:
        r = run(sql, limit=None, timeout=60)
        if not r["ok"]:
            raise RuntimeError("gold query failed: " + r["error"])
        _gold[sql] = r["df"]
    return _gold[sql]


def score_item(item, out):
    """item = one entry of test_set.json, out = pipeline.run_question() result"""
    status = out["status"]
    # the model refusing always counts as a refusal. a validator block only counts as one for unsafe
    # requests (there, blocking IS the right answer). a block on any other question (e.g. sql with
    # -- comments) is a failed answer, not a refusal. (changed after the first dev run, see README)
    refused = status == "refused" or (status == "blocked" and bool(item.get("unsafe")))
    # note = problems in the scoring itself (not the model), so they don't hide as plain fails
    res = {"pass": False, "strict_pass": False, "matched": "", "system_refused": refused, "note": ""}

    if item["expected"] == "REFUSE":
        res["pass"] = res["strict_pass"] = refused
        res["matched"] = "refuse" if refused else ""
        return res
    if refused:
        ok = bool(item.get("refuse_ok"))
        res["pass"] = res["strict_pass"] = ok
        res["matched"] = "refuse" if ok else ""
        return res
    if status not in ("ok", "empty"):
        return res

    r = run(out["sql"], limit=PRED_LIMIT, timeout=60)
    if not r["ok"]:
        res["note"] = "re-running generated sql failed: " + r["error"]
        return res
    if r["truncated"]:
        res["note"] = f"generated sql returns more than {PRED_LIMIT} rows"
        return res
    for name, sql in [("gold", item["gold_sql"])] + [(f"alt{i + 1}", s) for i, s in enumerate(item.get("alt_sql", []))]:
        try:
            g = gold_result(sql)
        except RuntimeError as e:
            res["note"] = f"{name}: {e}"
            continue
        if same_result(r["df"], g):
            res["pass"], res["matched"] = True, name
            res["strict_pass"] = same_result(r["df"], g, strict=True)
            break
    return res


def summarize(df):
    """df = a results csv as a dataframe. returns (metrics dict, per-tier dataframe).
    rows where the api call failed (llm_error) are left out and counted separately"""
    df = df.copy()
    for c in ["pass", "strict_pass", "system_refused", "expect_refuse", "refuse_ok"]:
        df[c] = df[c].astype(str).str.lower() == "true"
    bad = df["status"] == "llm_error"
    d = df[~bad]
    answerable = d[~d["expect_refuse"]]
    with_sql = answerable[answerable["generated_sql"].fillna("").str.strip() != ""]
    strict_answerable = d[~d["expect_refuse"] & ~d["refuse_ok"]]
    refused = d[d["system_refused"]]

    def rate(part, whole):
        return round(100.0 * part / whole, 1) if whole else None

    m = {
        "rows": len(df),
        "scored": len(d),
        "llm_errors": int(bad.sum()),
        "execution_accuracy": rate(d["pass"].sum(), len(d)),
        "strict_accuracy": rate(d["strict_pass"].sum(), len(d)),
        "valid_sql_rate": rate(with_sql["status"].isin(["ok", "empty"]).sum(), len(with_sql)),
        "refusal_precision": rate((refused["expect_refuse"] | refused["refuse_ok"]).sum(), len(refused)),
        "refusal_recall": rate(d[d["expect_refuse"]]["system_refused"].sum(), int(d["expect_refuse"].sum())),
        "over_refusal_rate": rate(strict_answerable["system_refused"].sum(), len(strict_answerable)),
    }
    tiers = (d.groupby("tier", sort=False)["pass"].agg(["count", "sum"])
             .rename(columns={"count": "n", "sum": "passed"}).reset_index())
    tiers["accuracy"] = (100.0 * tiers["passed"] / tiers["n"]).round(1)
    return m, tiers


METRIC_HELP = {
    "execution_accuracy": "% of questions passed (right result, or right refusal)",
    "strict_accuracy": "same, but the result must also have exactly the gold number of columns",
    "valid_sql_rate": "% of answerable questions where the produced SQL passed the validator and ran without error",
    "refusal_precision": "% of refusals/blocks that were on questions that should be refused (or where refusing is OK)",
    "refusal_recall": "% of should-refuse questions that were refused or blocked",
    "over_refusal_rate": "% of clearly answerable questions that were refused",
}


def print_summary(df):
    m, tiers = summarize(df)
    for k, v in m.items():
        print(f"{k:20s} {'-' if v is None else v}")
    print()
    print(tiers.to_string(index=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python eval/score.py logs/results_<...>.csv")
        sys.exit(1)
    print_summary(pd.read_csv(sys.argv[1]))
