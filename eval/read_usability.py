# summarises the filled usability sheet (review/usability_sheet.csv). it changes nothing.
# several raters: copy the 10 rows once per rater into the same csv, each with their own rater_name.
# prints: raters, ratings per measure (n, mean, median, min, max), per question, and rows with bad values
# writes logs/usability_summary.csv only when at least one rating exists
# run: python eval/read_usability.py
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SHEET = ROOT / "review" / "usability_sheet.csv"
OUT = ROOT / "logs" / "usability_summary.csv"
MEASURES = ["understandable_1to5", "useful_1to5"]


def main():
    if not SHEET.exists():
        sys.exit("review/usability_sheet.csv not found. Run python eval/make_usability_sheet.py first.")
    d = pd.read_csv(SHEET, encoding="utf-8-sig").astype(object).fillna("")
    bad = []
    for m in MEASURES:
        v = pd.to_numeric(d[m].astype(str).str.strip().replace("", None), errors="coerce")
        wrong = d[(d[m].astype(str).str.strip() != "") & ~v.isin([1, 2, 3, 4, 5])]
        bad += [f"{r['id']} ({r['rater_name'] or 'no name'}): {m} = {r[m]!r}" for _, r in wrong.iterrows()]
        d[m] = v.where(v.isin([1, 2, 3, 4, 5]))
    rated = d[d[MEASURES].notna().any(axis=1)]
    print(f"{len(d)} rows, {len(rated)} with at least one rating")
    if bad:
        print("values that are not 1-5 (left out):\n  " + "\n  ".join(bad))
    no_name = rated[rated["rater_name"].astype(str).str.strip() == ""]
    if len(no_name):
        print(f"{len(no_name)} rated row(s) have no rater_name")
    if rated.empty:
        print("No ratings yet. Nothing summarised.")
        return
    raters = sorted(set(rated["rater_name"].astype(str).str.strip()) - {""})
    print(f"raters ({len(raters)}): {', '.join(raters) or '-'}\n")
    rows = []
    for m in MEASURES:
        s = rated[m].dropna().astype(float)
        rows.append({"level": "all", "id": "", "measure": m, "n": len(s), "mean": round(s.mean(), 2) if len(s) else None,
                     "median": s.median() if len(s) else None, "min": s.min() if len(s) else None,
                     "max": s.max() if len(s) else None})
    for i, g in rated.groupby("id", sort=False):
        for m in MEASURES:
            s = g[m].dropna().astype(float)
            if len(s):
                rows.append({"level": "question", "id": i, "measure": m, "n": len(s), "mean": round(s.mean(), 2),
                             "median": s.median(), "min": s.min(), "max": s.max()})
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    out.to_csv(OUT, index=False)
    print(f"\nsaved {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
