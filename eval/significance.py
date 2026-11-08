# paired comparison of prompt versions on the same questions (exact mcnemar test)
# - uses the latest results file per version for the chosen split and model
# - with repeats, a question counts as passed if it passed in more than half of the repeats
# - p-values are holm-corrected because several pairs are tested at once
# run: python eval/significance.py --split test     (writes logs/significance_<split>.csv)

import argparse
import math
import sys
from itertools import combinations
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config


def mcnemar_exact(b, c):
    # two-sided exact binomial test on the discordant pairs
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj


def latest(df, split, model):
    d = df[(df["split"] == split) & (df["model"] == model) & (df["status"] != "llm_error")]
    keep = d.groupby("version")["run_id"].max()
    return d[d["run_id"].isin(keep.values)]


def per_question(d):
    # version -> {id: passed in more than half of the repeats}
    d = d.assign(p=d["pass"].astype(str).str.lower() == "true")
    out = {}
    for v, g in d.groupby("version"):
        out[v] = (g.groupby("id")["p"].mean() > 0.5).to_dict()
    return out


def per_repeat_acc(d):
    d = d.assign(p=d["pass"].astype(str).str.lower() == "true")
    acc = d.groupby(["version", "repeat"])["p"].mean() * 100
    return acc.groupby("version").agg(["mean", "std", "count"]).round(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["dev", "test"])
    ap.add_argument("--model", default=config.MODEL)
    a = ap.parse_args()

    files = sorted(config.LOGS.glob("results_*.csv"))
    if not files:
        sys.exit("No results files in logs/. Run eval/run_eval.py first.")
    d = latest(pd.concat(pd.read_csv(f) for f in files), a.split, a.model)
    if d["version"].nunique() < 2:
        sys.exit(f"Need results for at least 2 versions on the {a.split} split (model {a.model}).")

    pq = per_question(d)
    rows = []
    for v1, v2 in combinations(sorted(pq), 2):
        ids = sorted(set(pq[v1]) & set(pq[v2]))
        b = sum(pq[v1][i] and not pq[v2][i] for i in ids)   # v1 right, v2 wrong
        c = sum(not pq[v1][i] and pq[v2][i] for i in ids)   # v1 wrong, v2 right
        rows.append({"version_a": v1, "version_b": v2, "questions": len(ids),
                     "acc_a": round(100 * sum(pq[v1][i] for i in ids) / len(ids), 1),
                     "acc_b": round(100 * sum(pq[v2][i] for i in ids) / len(ids), 1),
                     "only_a_right": b, "only_b_right": c, "p_value": round(mcnemar_exact(b, c), 4)})
    adj = holm([r["p_value"] for r in rows])
    for r, p in zip(rows, adj):
        r["p_holm"] = round(p, 4)
        r["significant_0.05"] = p < 0.05

    out = pd.DataFrame(rows)
    path = config.LOGS / f"significance_{a.split}.csv"
    out.to_csv(path, index=False)
    print(f"split {a.split}, model {a.model}, runs: {', '.join(sorted(d['run_id'].unique()))}\n")
    print("accuracy over repeats (%):")
    print(per_repeat_acc(d).to_string())
    print("\npaired exact McNemar (per-question majority over repeats), Holm-corrected:")
    print(out.to_string(index=False))
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
