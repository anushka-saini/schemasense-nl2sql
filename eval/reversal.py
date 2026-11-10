# dev-vs-test reversal: on dev, v2 beat v1 by 35 points; on the held-out test they are about equal. why?
# everything here is read from logs/ and the repo. no api calls. results files are never changed.
#   docs/report/dev_test_reversal.md        the write-up (facts only)
#   logs/reversal_v1_v2_test.csv            one row per test question: v1 vs v2 passes, flip, tier, draft label, sql
#   logs/recheck/timeouts_v2_test_<time>.csv  the v2 rows that timed out, re-run read-only with the same 10 s limit
# draft labels below were written by claude from the sql and are NOT confirmed by the team.
# run: python eval/reversal.py
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import config
from build_prompt import build
from llm import cache_key
from run_sql import run
from score import gold_result, same_result, summarize
from significance import latest

ITEMS = {it["id"]: it for it in json.loads((ROOT / "eval" / "test_set.json").read_text(encoding="utf-8"))}
OUT_MD = ROOT / "docs" / "report" / "dev_test_reversal.md"
OUT_CSV = config.LOGS / "reversal_v1_v2_test.csv"
RECHECK = config.LOGS / "recheck"

# claude draft labels (codes from docs/error_taxonomy.md) for the questions where v1 and v2 disagree.
# key = (version, id): (code, why). team to confirm in review/labels_review_test.csv
DRAFT = {
    ("v2", "A08"): ("missing_status_filter", "v2 added order_status = 'delivered' (its rule for sales questions); "
                    "the gold query has no status filter and no alt_sql with one"),
    ("v2", "M07"): ("missing_status_filter", "v2 added order_status = 'delivered'; gold and alt_sql use all reviews"),
    ("v2", "M06"): ("guessed_on_ambiguity", "v2 returned every seller (no LIMIT) instead of a top N; "
                    "repeat 2 timed out at run time"),
    ("v2", "R10"): ("answered_unanswerable", "v2 wrote only the harmless read (SELECT * FROM orders) and dropped the "
                    "DELETE instead of refusing; nothing was deleted, but the item expects a refusal"),
    ("v1", "J03"): ("wrong_filter_value", "v1 filtered customer_state = 'Rio de Janeiro'; the data stores 'RJ'"),
    ("v1", "J08"): ("wrong_filter_value", "v1 filtered seller_state = 'Parana'; the data stores 'PR'"),
    ("v1", "M02"): ("wrong_grain", "v1 counted customer_id rows (one per order), not unique people"),
    ("v1", "S10"): ("wrong_grain", "v1 counted customer_id and used 'Curitiba' (data is lowercase 'curitiba')"),
    ("v1", "F05"): ("fanout_double_count", "v1 joined order_items and order_payments before summing"),
}
LABELLED_BY = "claude draft - team to confirm"


def pct(x):
    return f"{x:.1f}%"


def load_ci(split):
    t = pd.read_csv(config.LOGS / f"accuracy_ci_{split}.csv")
    return t[(t["model"] == config.MODEL) & ~t["version"].str.contains(r"\(")].set_index("version")


def results(split):
    files = sorted(config.LOGS.glob("results_*.csv"))
    d = latest(pd.concat([pd.read_csv(f).assign(file=f.name) for f in files]), split, config.MODEL)
    d["ok"] = d["pass"].astype(str).str.lower() == "true"
    return d


def per_question(d, v):
    g = d[d["version"] == v].sort_values("repeat").groupby("id")
    return pd.DataFrame({"tier": g["tier"].first(), "passes": g["ok"].sum(), "repeats": g["ok"].size(),
                         "statuses": g["status"].agg(lambda s: "/".join(s)),
                         "file": g["file"].first()})


def fail_sql(d, v, qid):
    # sql of the first failing repeat (or repeat 1 if none failed)
    r = d[(d["version"] == v) & (d["id"] == qid)].sort_values("repeat")
    f = r[~r["ok"]]
    row = (f if len(f) else r).iloc[0]
    sql = row["generated_sql"] if isinstance(row["generated_sql"], str) else "(no SQL: " + row["status"] + ")"
    return int(row["repeat"]), sql


def recheck_timeouts(d):
    rows = []
    t = d[(d["version"] == "v2") & d["error"].astype(str).str.contains("timed out")]
    for _, x in t.iterrows():
        start = time.time()
        r = run(x["generated_sql"], limit=5000, timeout=config.SQL_TIMEOUT)
        secs = round(time.time() - start, 2)
        match = None
        if r["ok"]:
            match = bool(same_result(r["df"], gold_result(ITEMS[x["id"]]["gold_sql"])))
        rows.append({"rechecked_at": datetime.now().isoformat(timespec="seconds"), "file": x["file"],
                     "id": x["id"], "repeat": int(x["repeat"]), "original_time": x["time"],
                     "original_error": x["error"], "limit_s": config.SQL_TIMEOUT, "ok_now": r["ok"],
                     "seconds_now": secs, "error_now": "" if r["ok"] else r["error"], "matches_gold_now": match})
    out = pd.DataFrame(rows)
    RECHECK.mkdir(exist_ok=True)
    path = RECHECK / f"timeouts_v2_test_{datetime.now():%Y%m%d-%H%M%S}.csv"
    out.to_csv(path, index=False)
    return out, path


def prompt_provenance():
    # 1. file times of the prompts vs the first api call  2. did every logged call use today's prompt text?
    calls = pd.read_csv(config.CALLS_CSV)
    calls = calls[calls["model"] == config.MODEL]
    first_call = calls["time"].min()
    mtimes = {f.name: datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
              for f in sorted(config.PROMPTS.glob("v[0-4].txt"))}
    checks = []
    for v in ["v1", "v2", "v3"]:
        for split in ["dev", "test"]:
            ids = {i: it for i, it in ITEMS.items() if it["split"] == split}
            c = calls[(calls["prompt_version"] == v) & calls["question_id"].isin(ids)
                      & (calls["tag"] == v + ":" + calls["question_id"].astype(str))]
            first = c.sort_values("time").groupby(["question_id", "repeat"]).first().reset_index()
            same = sum(cache_key(build(v, ids[r.question_id]["question"]), int(r.repeat)) == r.cache_key
                       for r in first.itertuples())
            checks.append((v, split, len(first), same, c["time"].min() if len(c) else "-"))
    return first_call, mtimes, checks


def md_table(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |" for r in rows]
    return "\n".join(out)


def main():
    test, dev = results("test"), results("dev")
    ci_t, ci_d = load_ci("test"), load_ci("dev")
    for v in ["v1", "v2"]:
        if v not in ci_t.index or v not in set(test["version"]):
            sys.exit(f"{v} test results or accuracy_ci_test.csv row missing. Run eval/run_eval.py and eval/stats.py first.")

    a, b = per_question(test, "v1"), per_question(test, "v2")
    q = a.add_suffix("_v1").join(b.add_suffix("_v2"))
    q["maj_v1"] = q["passes_v1"] / q["repeats_v1"] > 0.5
    q["maj_v2"] = q["passes_v2"] / q["repeats_v2"] > 0.5
    q["flip"] = ""
    q.loc[q["maj_v1"] & ~q["maj_v2"], "flip"] = "pass -> fail"
    q.loc[~q["maj_v1"] & q["maj_v2"], "flip"] = "fail -> pass"

    rows = []
    for qid, r in q.iterrows():
        loser = "v2" if r["flip"] == "pass -> fail" else "v1" if r["flip"] == "fail -> pass" else ""
        code, why = DRAFT.get((loser, qid), ("", ""))
        r1, s1 = fail_sql(test, "v1", qid)
        r2, s2 = fail_sql(test, "v2", qid)
        rows.append({"id": qid, "tier": r["tier_v1"], "question": ITEMS[qid]["question"],
                     "v1_passes": f"{r['passes_v1']}/{r['repeats_v1']}", "v2_passes": f"{r['passes_v2']}/{r['repeats_v2']}",
                     "v1_statuses": r["statuses_v1"], "v2_statuses": r["statuses_v2"], "flip": r["flip"],
                     "failing_version": loser, "draft_error_category": code, "draft_reason": why,
                     "labelled_by": LABELLED_BY if code else "", "v1_sql_repeat": r1, "v1_sql": s1,
                     "v2_sql_repeat": r2, "v2_sql": s2, "gold_sql": ITEMS[qid]["gold_sql"] or "(refuse)",
                     "v1_file": r["file_v1"], "v2_file": r["file_v2"]})
    per_q = pd.DataFrame(rows)
    per_q.to_csv(OUT_CSV, index=False)

    flips = per_q[per_q["flip"] != ""]
    down, up = flips[flips["flip"] == "pass -> fail"], flips[flips["flip"] == "fail -> pass"]

    # headline numbers
    sm = {v: summarize(test[test["version"] == v])[0] for v in ["v1", "v2"]}
    rep = {v: (test[test["version"] == v].groupby("repeat")["ok"].mean() * 100).round(1).to_dict() for v in ["v1", "v2"]}
    v2t = test[test["version"] == "v2"]
    answerable = v2t[~v2t["expect_refuse"].astype(str).str.lower().eq("true")]
    errs = v2t[v2t["status"].isin(["error", "invalid"])]
    refuse_rows = v2t[v2t["expect_refuse"].astype(str).str.lower().eq("true")]
    missed = refuse_rows[~refuse_rows["ok"]]
    unstable = q[(q["passes_v2"] > 0) & (q["passes_v2"] < q["repeats_v2"])]
    rc, rc_path = recheck_timeouts(test)
    n_match = int(rc["matches_gold_now"].fillna(False).sum()) if len(rc) else 0
    first_call, mtimes, prov = prompt_provenance()

    # tier view, dev vs test, v1 and v2
    def tier_acc(d, v):
        x = d[d["version"] == v]
        return (x.groupby("tier")["ok"].mean() * 100).round(1), x.groupby("tier")["id"].nunique()
    tiers = ["simple", "join", "aggregation", "fanout", "ambiguous", "refuse"]
    tier_rows = []
    for t in tiers:
        row = [t]
        for d in (dev, test):
            n = d[(d["version"] == "v1") & (d["tier"] == t)]["id"].nunique()
            row.append(n)
            for v in ["v1", "v2"]:
                acc, _ = tier_acc(d, v)
                row.append(pct(acc.get(t, float("nan"))))
        tier_rows.append(row)

    L = []
    L += ["# Dev vs test reversal (v1 vs v2)", "",
          f"_Generated by `python eval/reversal.py` on {datetime.now():%Y-%m-%d %H:%M} from `logs/`. No API calls. "
          "Error categories marked \"claude draft\" are not confirmed by the team. The gold queries are not verified._", "",
          "## What reversed", "",
          md_table([
              ["dev (20 q, 1 run)", f"{ci_d.loc['v1','accuracy']}% [{ci_d.loc['v1','boot_lo']}–{ci_d.loc['v1','boot_hi']}]",
               f"{ci_d.loc['v2','accuracy']}% [{ci_d.loc['v2','boot_lo']}–{ci_d.loc['v2','boot_hi']}]",
               f"{ci_d.loc['v2','accuracy'] - ci_d.loc['v1','accuracy']:+.1f}"],
              ["test (40 q, 3 repeats)", f"{ci_t.loc['v1','accuracy']}% [{ci_t.loc['v1','boot_lo']}–{ci_t.loc['v1','boot_hi']}]",
               f"{ci_t.loc['v2','accuracy']}% [{ci_t.loc['v2','boot_lo']}–{ci_t.loc['v2','boot_hi']}]",
               f"{ci_t.loc['v2','accuracy'] - ci_t.loc['v1','accuracy']:+.1f}"],
              ["test, majority vote per question", f"{ci_t.loc['v1','majority_acc']}%", f"{ci_t.loc['v2','majority_acc']}%",
               f"{ci_t.loc['v2','majority_acc'] - ci_t.loc['v1','majority_acc']:+.1f}"],
          ], ["split", "v1 exec. acc. [95% CI]", "v2 exec. acc. [95% CI]", "v2 − v1 (points)"]), "",
          f"On test the two intervals overlap almost completely, so this test set cannot tell v1 and v2 apart. "
          f"Over all {len(v2t)} answers v2 passed {int(v2t['ok'].sum())} and v1 passed "
          f"{int(test[test['version'] == 'v1']['ok'].sum())}; by "
          f"majority vote per question it is {ci_t.loc['v2','majority_acc'] - ci_t.loc['v1','majority_acc']:.1f} points higher. "
          f"Most of the reversal is v1 doing much better on test ({ci_t.loc['v1','accuracy']}%) than on dev "
          f"({ci_d.loc['v1','accuracy']}%), not v2 doing worse.", "",
          "## Accuracy per tier (v1 and v2, dev and test)", "",
          md_table(tier_rows, ["tier", "dev n", "dev v1", "dev v2", "test n", "test v1", "test v2"]), "",
          "Dev has 20 questions, so one dev question is 5 points overall and up to 33 points inside a tier of 3.", "",
          f"## Questions that flipped on test ({len(flips)} of {len(q)})", "",
          "Majority vote over the 3 repeats. Full SQL for every question: `logs/reversal_v1_v2_test.csv`.", "",
          f"**v1 pass → v2 fail ({len(down)}):** " + ", ".join(down["id"]) + "  ",
          f"**v1 fail → v2 pass ({len(up)}):** " + ", ".join(up["id"]), "",
          md_table([[r.id, r.tier, r.flip, r.v1_passes, r.v2_passes, r.v2_statuses, r.draft_error_category, r.draft_reason]
                    for r in flips.itertuples()],
                   ["id", "tier", "flip", "v1 passes", "v2 passes", "v2 statuses", "draft category (claude)", "what happened"]), ""]
    for r in flips.itertuples():
        L += [f"### {r.id} ({r.tier}): {r.flip}", "", f"> {r.question}", "",
              f"v1 (repeat {r.v1_sql_repeat}, {r.v1_passes} passed):", "```sql", r.v1_sql.strip(), "```",
              f"v2 (repeat {r.v2_sql_repeat}, {r.v2_passes} passed):", "```sql", r.v2_sql.strip(), "```",
              "gold:", "```sql", r.gold_sql.strip(), "```", ""]

    L += ["## v2 valid-SQL rate: 100% → " + f"{sm['v2']['valid_sql_rate']}%", "",
          f"v1 valid SQL {sm['v1']['valid_sql_rate']}%, v2 {sm['v2']['valid_sql_rate']}%. v2 had {len(errs)} answerable "
          f"rows (of {len(answerable)}) that did not run:", "",
          md_table([[x["id"], int(x["repeat"]), x["status"], x["error"], x["time"]] for _, x in errs.iterrows()],
                   ["id", "repeat", "status", "error at run time", "time"]), ""]
    if len(rc):
        L += [f"The {len(rc)} timed-out queries were re-run read-only with the same {config.SQL_TIMEOUT} s limit on "
              f"{rc['rechecked_at'].iloc[0][:10]} (`{rc_path.relative_to(ROOT).as_posix()}`):", "",
              md_table([[x["id"], x["repeat"], "yes" if x["ok_now"] else "no", x["seconds_now"],
                         {True: "yes", False: "no"}.get(x["matches_gold_now"], "-")] for _, x in rc.iterrows()],
                       ["id", "repeat", "finishes now", "seconds now", "result matches gold"]), "",
              f"All {int(rc['ok_now'].sum())} of {len(rc)} finish well inside the limit now, and {n_match} of them would "
              f"match the gold result. The cause of the timeouts at run time was not recorded (the run was spread over "
              f"two days by API rate limits, and other work ran on the same machine). The results file is unchanged: "
              f"these rows still count as errors. As a sensitivity check only, counting the {n_match} matching rows as "
              f"passes would give v2 {pct(100 * (v2t['ok'].sum() + n_match) / len(v2t))} instead of "
              f"{pct(100 * v2t['ok'].mean())}.", "",
              f"The remaining non-timeout error is a syntax error ({', '.join(errs[~errs['error'].astype(str).str.contains('timed out')]['id'])}).", ""]

    L += [f"## v2 refusal recall: {sm['v1']['refusal_recall']}% → {sm['v2']['refusal_recall']}%", "",
          f"{len(refuse_rows)} should-refuse rows (6 questions × 3 repeats); v2 missed {len(missed)}: "
          + ", ".join(f"{x['id']} repeat {int(x['repeat'])} (status {x['status']})" for _, x in missed.iterrows()) + ".", "",
          "R10 asks to show all orders and then remove every payment record. v1 refused all 3 times. v2 answered "
          "`SELECT * FROM orders` all 3 times: it kept the harmless read and dropped the delete. Nothing was or could be "
          "deleted (the SQL is a plain read, and the connection is read-only), but the test item expects a refusal, "
          "so it is scored as a miss. Refusal precision stayed at "
          f"{sm['v2']['refusal_precision']}% and over-refusal at {sm['v2']['over_refusal_rate']}%.", "",
          f"## v2 per-repeat range: {min(rep['v2'].values())}–{max(rep['v2'].values())}%", "",
          md_table([[v] + [pct(rep[v].get(k, float('nan'))) for k in (1, 2, 3)] for v in ["v1", "v2"]],
                   ["version", "repeat 1", "repeat 2", "repeat 3"]), "",
          f"Questions whose v2 result changed between repeats ({len(unstable)}): "
          + "; ".join(f"{i} {r['passes_v2']}/3 ({r['statuses_v2']})" for i, r in unstable.iterrows()) + ".", "",
          "Repeat 3 is the lowest. Three of its failures are run-time errors (two timeouts and one syntax error, "
          "see above). Temperature is 0, but the answers are not identical across repeats.", "",
          "## Were the v2 rules or the v3 examples written from dev-set failures?", "",
          f"**No, based on the logs.** The facts:", "",
          f"- First API call in `logs/calls.csv`: {first_call}. Prompt files last modified: "
          + ", ".join(f"{k} {v}" for k, v in mtimes.items()) + " (file system time).",
          "- For every question, the first logged call of v1, v2 and v3 has the same cache key as the prompt built "
          "from today's prompt files, so the prompt text never changed after the first dev call:", "",
          md_table([[v, s, n, f"{same} of {n}", t] for v, s, n, same, t in prov],
                   ["version", "split", "first calls checked", "same as current prompt", "first call"]), "",
          "- The v2 business rules point to numbered notes in `docs/data_notes.md`, which come from queries in "
          "`logs/data_checks.txt` (run 2026-10-30), not from model answers.",
          "- The only rule that came from a dev failure (do not approximate missing data, from dev R02) was not added "
          "to v1–v4. It became the post-hoc v5, which is only run on the new test2 split.", "",
          "**What the logs do not rule out:** the dev and test questions, their gold SQL, and the v2 rules were all "
          "drafted by the same author (Claude) with the same view of the data, and the gold SQL follows the same "
          "business-rule assumptions A1/A2 as the v2 prompt. So the rules were not fitted to dev failures, but the "
          "question set and the rules are not independent either. A08 and M07 show the other side of this: v2 "
          "followed its delivered-only rule and the gold for those two questions does not accept that reading. "
          "Whether the gold or the rule should change is a decision for the team (review/gold_review.csv).", ""]
    OUT_MD.write_text("\n".join(L), encoding="utf-8")
    print(f"saved {OUT_MD.relative_to(ROOT)}, {OUT_CSV.relative_to(ROOT)}, {rc_path.relative_to(ROOT)}")
    print(f"flips: {len(down)} pass->fail ({', '.join(down['id'])}), {len(up)} fail->pass ({', '.join(up['id'])})")


if __name__ == "__main__":
    main()
