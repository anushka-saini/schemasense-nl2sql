# makes the sheets the team uses to check the gold queries and the draft error labels by hand
#   review/gold_review.csv   + review/gold_review.html     one row per question (60)
#   review/labels_review.csv + review/labels_review.html   one row per failed answer with a draft label
# the csv files are the ones to fill in (reviewer, verdict, notes). the html is only for reading.
# no api calls. an existing csv is never overwritten (it may already hold the team's answers), use --force
# run: python eval/make_review_sheets.py   then fill the csv, then: python eval/read_review.py

import argparse
import html
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import config
from run_sql import run

OUT = ROOT / "review"
ITEMS = json.loads((ROOT / "eval" / "test_set.json").read_text(encoding="utf-8"))
TEST2_FILE = ROOT / "eval" / "test2_set.json"  # 20 new questions for the post-hoc v5 check
ITEMS2 = json.loads(TEST2_FILE.read_text(encoding="utf-8")) if TEST2_FILE.exists() else []
BY_ID = {it["id"]: it for it in ITEMS + ITEMS2}
REVIEW_COLS = ["reviewer", "verdict (ok/fix/unsure)", "notes"]


def assumptions(it):
    # which business rules / assumptions the gold query depends on (read from the sql itself)
    s = (it.get("gold_sql") or "").lower()
    a = []
    if it["expected"] == "REFUSE":
        a.append("should be refused" + (" (unsafe request)" if it.get("unsafe") else " (data not in the schema)"))
    if "order_status = 'delivered'" in s:
        a.append("ASSUMPTION A1: sales / delivery = delivered orders only")
    if "price" in s and "sum(" in s:
        a.append("ASSUMPTION A2: revenue = SUM(order_items.price), freight not included")
    if "payment_value" in s:
        a.append("ASSUMPTION A2: money paid = SUM(order_payments.payment_value)")
    if "customer_unique_id" in s:
        a.append("rule: one customer = customer_unique_id")
    if "product_category_name_english" in s:
        a.append("rule: English category names from the translation table")
    if "julianday" in s:
        a.append("rule: days = julianday difference, NULL delivered dates left out")
    if it.get("alt_sql"):
        a.append(f"ambiguous: {len(it['alt_sql'])} other reading(s) also accepted (alt_sql)")
    if it.get("refuse_ok"):
        a.append("refusing is also accepted")
    return "; ".join(a)


def preview(sql):
    if not sql:
        return "", ""
    # long time limit: a few gold queries (fan-out ones) are slow when the pc is busy
    r = run(sql, limit=None, timeout=300)
    if not r["ok"]:
        return f"ERROR: {r['error']}", ""
    return r["df"].head(5).to_csv(index=False, lineterminator="\n").strip(), len(r["df"])


def gold_sheet(items=None):
    rows = []
    for it in (ITEMS if items is None else items):
        p, n = preview(it.get("gold_sql"))
        rows.append({"id": it["id"], "tier": it["tier"], "split": it["split"], "question": it["question"],
                     "gold_sql": it.get("gold_sql", ""), "alt_sql": "\n---\n".join(it.get("alt_sql", [])),
                     "gold_rows": n, "gold_preview_first_5_rows": p, "assumptions_used": assumptions(it),
                     "item_notes": it.get("notes", ""), "verified_in_test_set": it.get("verified", False),
                     **{c: "" for c in REVIEW_COLS}})
    return pd.DataFrame(rows)


def labels_sheet(splits=("dev",)):
    # labels_review.csv = dev (made first), labels_review_test.csv = test. separate files so a filled sheet is never touched
    parts = []
    for split in splits:
        f = config.LOGS / f"error_labels_{split}.csv"
        if f.exists():
            parts.append(pd.read_csv(f).astype(object).fillna("").assign(split=split))
    if not parts:
        return pd.DataFrame()
    d = pd.concat(parts, ignore_index=True)
    # the same wrong sql in several repeats is one thing to check
    d["generated_sql"] = d["generated_sql"].astype(str)
    g = (d.groupby(["split", "version", "id", "generated_sql", "error_category"], sort=False)
         .agg(repeats=("repeat", lambda x: ",".join(str(i) for i in sorted(x))), tier=("tier", "first"),
              question=("question", "first"), status=("status", "first"), error=("error", "first"),
              labelled_by=("labelled_by", "first"), results_file=("file", "first"))
         .reset_index())
    g["gold_sql"] = g["id"].map(lambda i: BY_ID[i].get("gold_sql", ""))
    g = g.rename(columns={"error_category": "draft_label"})
    cols = ["split", "version", "id", "repeats", "tier", "question", "status", "error", "generated_sql", "gold_sql",
            "draft_label", "labelled_by", "results_file", "reviewer", "verdict (ok/fix/unsure)", "correct_label", "notes"]
    for c in ["reviewer", "verdict (ok/fix/unsure)", "correct_label", "notes"]:
        g[c] = ""
    return g[cols]


def html_page(df, title, intro, code_cols):
    e = html.escape
    head = "".join(f"<th>{e(c)}</th>" for c in df.columns)
    body = ""
    for r in df.itertuples(index=False):
        cells = ""
        for c, v in zip(df.columns, r):
            v = "" if pd.isna(v) else str(v)
            cells += f"<td><pre>{e(v)}</pre></td>" if c in code_cols else f"<td>{e(v)}</td>"
        body += f"<tr>{cells}</tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>
:root{{--bg:#f7f8f9;--fg:#1b1f24;--muted:#5b6672;--line:#dfe3e8;--panel:#fff;--code:#eef1f4}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111418;--fg:#e6e8eb;--muted:#9aa3ad;--line:#2a313a;--panel:#171b21;--code:#1e242c}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 "Segoe UI",system-ui,sans-serif;margin:0;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 6px}} p{{color:var(--muted);max-width:80ch}}
.tw{{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--panel)}}
table{{border-collapse:collapse;font-size:12.5px}} th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:var(--panel);font-weight:600}} td{{min-width:70px;max-width:420px}}
pre{{margin:0;white-space:pre-wrap;font:12px Consolas,monospace;background:var(--code);padding:4px 6px;border-radius:4px}}
</style></head><body><h1>{e(title)}</h1><p>{intro}</p><div class="tw"><table><thead><tr>{head}</tr></thead>
<tbody>{body}</tbody></table></div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite existing csv sheets (their answers are lost)")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    for name, make, title, intro, code in [
        ("gold_review", gold_sheet, "Gold query review",
         "One row per question in eval/test_set.json. Fill <b>reviewer</b>, <b>verdict</b> (ok / fix / unsure) and "
         "<b>notes</b> in review/gold_review.csv, not in this page. Check the gold SQL answers the question as a "
         "person would read it, and that the assumptions are ones the team agrees with. Only a person marks "
         "<code>verified: true</code> in eval/test_set.json.",
         {"gold_sql", "alt_sql", "gold_preview_first_5_rows"}),
        ("gold_review_test2", lambda: gold_sheet(ITEMS2) if ITEMS2 else pd.DataFrame(), "Gold query review: test2",
         "The 20 new questions in eval/test2_set.json (post-hoc v5 check), drafted by Claude and not verified. Same "
         "columns as gold_review. Fill <b>reviewer</b>, <b>verdict</b> and <b>notes</b> in review/gold_review_test2.csv. "
         "Only a person marks <code>verified: true</code> in eval/test2_set.json.",
         {"gold_sql", "alt_sql", "gold_preview_first_5_rows"}),
        ("labels_review_test", lambda: labels_sheet(("test",)), "Failure label review: held-out test",
         "Same as labels_review, for the held-out test split. The draft label was written by Claude. Fill "
         "<b>reviewer</b>, <b>verdict</b>, <b>correct_label</b> and <b>notes</b> in review/labels_review_test.csv.",
         {"generated_sql", "gold_sql", "error"}),
        ("labels_review", labels_sheet, "Failure label review",
         "One row per distinct failed answer (same SQL in several repeats = one row). The draft label was written "
         "by Claude. Fill <b>reviewer</b>, <b>verdict</b> (ok / fix / unsure), <b>correct_label</b> (when fix, codes "
         "in docs/error_taxonomy.md) and <b>notes</b> in review/labels_review.csv.",
         {"generated_sql", "gold_sql", "error"}),
    ]:
        csv_path = OUT / f"{name}.csv"
        df = make()
        if df.empty:
            print(f"{name}: nothing to review yet")
            continue
        if csv_path.exists() and not a.force:
            print(f"{csv_path.name} already exists, not overwritten (use --force). html page refreshed only.")
        else:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")  # utf-8-sig so excel opens it right
            print(f"{csv_path.name}: {len(df)} rows")
        (OUT / f"{name}.html").write_text(html_page(df, title, intro, code), encoding="utf-8")


if __name__ == "__main__":
    main()
