# reads the filled review sheets back and reports progress. it changes nothing.
#   review/gold_review.csv   -> how many gold queries were checked, which are flagged "fix" or "unsure"
#   review/labels_review.csv -> how many draft labels were confirmed, which need a different label
# setting verified: true in eval/test_set.json stays a manual step for a team member
# run: python eval/read_review.py

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REVIEW = ROOT / "review"
VERDICT = "verdict (ok/fix/unsure)"
OK_VERDICTS = {"ok", "fix", "unsure", ""}


def load(name):
    p = REVIEW / f"{name}.csv"
    if not p.exists():
        print(f"{p.name}: not found. Run python eval/make_review_sheets.py first.\n")
        return None
    d = pd.read_csv(p, encoding="utf-8-sig").astype(object).fillna("")
    d[VERDICT] = d[VERDICT].astype(str).str.strip().str.lower()
    return d


def report(d, name, key_cols):
    bad = d[~d[VERDICT].isin(OK_VERDICTS)]
    if len(bad):
        print(f"  WARNING: {len(bad)} row(s) with a verdict that is not ok / fix / unsure: "
              + ", ".join(sorted(set(bad[VERDICT]))))
    no_name = d[(d[VERDICT] != "") & (d["reviewer"].astype(str).str.strip() == "")]
    counts = d[VERDICT].replace("", "(not reviewed)").value_counts()
    print(f"{name}: {len(d)} rows")
    for k, v in counts.items():
        print(f"  {k:15s} {v}")
    if len(no_name):
        print(f"  {len(no_name)} row(s) have a verdict but no reviewer name")
    for flag in ["fix", "unsure"]:
        f = d[d[VERDICT] == flag]
        if len(f):
            print(f"\n  flagged '{flag}':")
            for _, r in f.iterrows():
                print("   - " + " / ".join(str(r[c]) for c in key_cols) + (f"  notes: {r['notes']}" if r["notes"] else ""))
    print()


def main():
    g = load("gold_review")
    if g is not None:
        report(g, "gold_review.csv", ["id", "split"])
        ok = g[g[VERDICT] == "ok"]["id"].tolist()
        if ok:
            print(f"  marked ok ({len(ok)}): {', '.join(ok)}")
            print("  a team member can now set \"verified\": true for these ids in eval/test_set.json by hand.\n")
    l = load("labels_review")
    if l is not None:
        report(l, "labels_review.csv", ["split", "version", "id", "draft_label", "correct_label"])
        fix = l[(l[VERDICT] == "fix") & (l["correct_label"].astype(str).str.strip() == "")]
        if len(fix):
            print(f"  {len(fix)} 'fix' row(s) have no correct_label yet\n")
    if g is None and l is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
