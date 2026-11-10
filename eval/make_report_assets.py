# builds the report pieces from the repo and the logs (nothing typed from memory). no api calls.
# a number that does not exist yet is written as PENDING
#   docs/report/abstract.md            one-page synopsis
#   docs/report/prompt_diff.md         v0 -> v4: what changed, why, targeted failure, accuracy before/after
#   docs/report/v0_baseline.md         v0 strict format vs lenient extractor, side by side
#   docs/report/failure_examples.md    5 real failures and which prompt change fixed them (or not)
#   docs/report/prompts_appendix.md    full text of every prompt + the schema text that gets filled in
#   docs/report/reproducibility.md     model, settings, cache, package versions, exact commands
#   docs/demo_script.md                demo script + shot list with real outputs from the cache
# run: python eval/make_report_assets.py   (re-run after new results)

import json
import sys
from importlib import metadata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import config
import stats
from pipeline import check_and_run
from run_sql import run
from schema import create_sql, names_only
from score import summarize

OUT = ROOT / "docs" / "report"
VERS = ["v0", "v1", "v2", "v3", "v4"]
ITEMS = {it["id"]: it for it in json.loads((ROOT / "eval" / "test_set.json").read_text(encoding="utf-8"))}
P = "PENDING"

# what changed in each version and why. taken from prompts/CHANGELOG.md; codes from docs/error_taxonomy.md
CHANGES = {
    "v0": ("Control: the question only, no schema, no rules.",
           "Floor: what the model does when it knows nothing about the database.", []),
    "v1": ("Baseline instructions (one SELECT, never write, CANNOT_ANSWER, SQL then a one-line explanation) "
           "+ table and column names.",
           "The model needs the real names and a fixed answer format.",
           ["format_failure", "hallucinated_column"]),
    "v2": ("Real CREATE TABLE text + join keys + business rules (unique customers, fan-out, delivered = sales, "
           "revenue, English categories, text dates, city/state formats).",
           "Some facts are only visible in the data, not in the names.",
           ["fanout_double_count", "wrong_grain", "missing_status_filter", "language_mismatch",
            "wrong_filter_value", "date_handling", "wrong_join_key"]),
    "v3": ("5 worked examples in the answer format (unique customers, per-order CTEs, English category, "
           "delivery days, a CANNOT_ANSWER case).",
           "Rules say what to do; examples show it.",
           ["wrong_grain", "fanout_double_count", "language_mismatch", "date_handling", "answered_unanswerable"]),
    "v4": ("Same text as v3 + a repair loop: invalid SQL, a SQLite error or 0 rows are sent back with the exact "
           "error, at most 2 times.",
           "Many failures are small and SQLite names the problem exactly.",
           ["hallucinated_column", "wrong_filter_value"]),
}

# real failures picked from the dev split (labels are Claude drafts until the team confirms them)
EXAMPLES = [("v0", "S03"), ("v1", "F01"), ("v1", "M04"), ("v2", "A10"), ("v2", "R02")]


# ---------------- reading results ----------------
def read(folder):
    fs = sorted(folder.glob("results_*.csv"))
    return pd.concat([pd.read_csv(f).assign(file=f.name) for f in fs], ignore_index=True) if fs else pd.DataFrame()


RES = read(config.LOGS)
LEN = read(config.LOGS / "lenient")


def latest(df, split, model=config.MODEL):
    if df.empty:
        return df
    d = df[(df["split"] == split) & (df["model"] == model)]
    keep = d.groupby("version")["run_id"].max()
    return d[d["run_id"].isin(keep.values)]


def ci_table(split):
    try:
        return stats.table(split)
    except Exception:
        return pd.DataFrame()


CI = {"dev": ci_table("dev"), "test": ci_table("test"), "test2": ci_table("test2")}
GAP_SENTENCE = ("Strict accuracy also requires exactly the gold number of columns; the gap to execution accuracy is "
                "answers with the right rows plus extra columns (for example a count next to the name asked for).")


def acc(split, v, model=config.MODEL):
    t = CI[split]
    if t.empty:
        return P
    r = t[(t["version"] == v) & (t["model"] == model)]
    if r.empty:
        return P
    r = r.iloc[0]
    return f"{r['accuracy']:.1f}% [{r['boot_lo']:.1f}–{r['boot_hi']:.1f}]"


def sacc(split, v, model=config.MODEL):
    # strict accuracy (same rows AND the same number of columns as gold), same bootstrap over questions
    t = CI[split]
    if t.empty or "strict_acc" not in t:
        return P
    r = t[(t["version"] == v) & (t["model"] == model)]
    if r.empty:
        return P
    r = r.iloc[0]
    return f"{r['strict_acc']:.1f}% [{r['strict_lo']:.1f}–{r['strict_hi']:.1f}]"


def labels(split):
    f = config.LOGS / f"error_labels_{split}.csv"
    return pd.read_csv(f).astype(object).fillna("") if f.exists() else pd.DataFrame()


def md_table(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in r) + " |")
    return "\n".join(out)


def code(s, lang="sql"):
    return f"```{lang}\n{str(s).strip()}\n```"


# ---------------- pieces ----------------
def prompt_diff():
    lab = labels("dev")
    rows = []
    for i, v in enumerate(VERS):
        change, why, codes = CHANGES[v]
        prev = VERS[i - 1] if i else None
        if codes and not lab.empty and prev:
            before = int(lab[(lab["version"] == prev) & lab["error_category"].isin(codes)].shape[0])
            after = int(lab[(lab["version"] == v) & lab["error_category"].isin(codes)].shape[0])
            counts = f"{before} → {after}"
        else:
            counts = "–"
        rows.append({"version": v, "what changed": change, "why": why,
                     "targets": ", ".join(f"`{c}`" for c in codes) or "–",
                     "targeted dev labels (prev → this)": counts,
                     "dev acc. [95% CI]": acc("dev", v), "test acc. [95% CI]": acc("test", v)})
    return pd.DataFrame(rows)


def v0_baseline():
    rows = []
    for split in ["dev", "test"]:
        for name, df in [("strict format (as scored)", latest(RES, split)), ("lenient extractor", latest(LEN, split))]:
            g = df[df["version"] == "v0"] if not df.empty else df
            if g.empty:
                rows.append({"split": split, "extractor": name, "execution acc. [95% CI]": P})
                continue
            m, _ = summarize(g)
            label = "v0" if name.startswith("strict") else "v0 (lenient extractor)"
            t = CI[split]
            r = t[(t["version"] == label) & (t["model"] == config.MODEL)].iloc[0]
            st = g["status"].value_counts()
            rows.append({"split": split, "extractor": name, "answers": len(g),
                         "execution acc. [95% CI]": f"{m['execution_accuracy']:.1f}% [{r['boot_lo']:.1f}–{r['boot_hi']:.1f}]",
                         "valid SQL": f"{m['valid_sql_rate']:.1f}%",
                         "statuses": ", ".join(f"{k} {n}" for k, n in st.items())})
    return pd.DataFrame(rows)


def unknown_tables(split="test"):
    g = latest(LEN, split)
    if g.empty:
        return P
    return int(g["error"].fillna("").str.startswith("unknown table").sum()), len(g)


def example(v, qid):
    d = latest(RES, "dev")
    row = d[(d["version"] == v) & (d["id"] == qid)].iloc[0]
    lab = labels("dev")
    lr = lab[(lab["version"] == v) & (lab["id"] == qid)]
    passes = {u: str(d[(d["version"] == u) & (d["id"] == qid)]["pass"].iloc[0]).lower() == "true" for u in VERS}
    later = VERS[VERS.index(v) + 1:]
    fixed = next((u for u in later if all(passes[w] for w in VERS[VERS.index(u):])), None)
    good = d[(d["version"] == fixed) & (d["id"] == qid)].iloc[0] if fixed else None
    return {"v": v, "id": qid, "question": row["question"], "status": row["status"], "error": row["error"],
            "sql": row["generated_sql"], "label": lr["error_category"].iloc[0] if len(lr) else "",
            "gold": ITEMS[qid].get("gold_sql", ""), "passes": passes, "fixed": fixed,
            "fixed_sql": good["generated_sql"] if good is not None else "", "file": row["file"]}


def failure_examples_md():
    out = ["# Worked failure examples", "",
           "Real failures from the dev split (latest run per version). The error label is Claude's draft and needs a "
           "team check (review/labels_review.csv). \"Fixed by\" = the first later version from which every version "
           "passes this question.", ""]
    for n, (v, qid) in enumerate(EXAMPLES, 1):
        e = example(v, qid)
        line = " · ".join(f"{u} {'pass' if e['passes'][u] else 'fail'}" for u in VERS)
        out += [f"## {n}. {qid} ({ITEMS[qid]['tier']}), failed in {v}", "",
                f"**Question:** {e['question']}", "",
                f"**{v} answer** (status `{e['status']}`{', ' + str(e['error']) if str(e['error']) not in ('', 'nan') else ''}):",
                code(e["sql"] if str(e["sql"]) != "nan" else "(no SQL)"), "",
                f"**Draft error label:** `{e['label'] or 'none'}`", "",
                "**Gold:**", code(e["gold"] or "(should be refused)"), "",
                f"**Across versions:** {line}", ""]
        if e["fixed"]:
            out += [f"**Fixed by {e['fixed']}:** {CHANGES[e['fixed']][0]}", "",
                    f"{e['fixed']} answer:", code(e["fixed_sql"] if str(e["fixed_sql"]) != "nan" else "(refused)"), ""]
        else:
            out += ["**Not fixed by any later version.**", ""]
        out += [f"_Source: logs/{e['file']}_", ""]
    return "\n".join(out)


def prompts_appendix_md():
    out = ["# Appendix: full prompt text", "",
           "Exact files from `prompts/`. `{question}` is replaced by the user's question and `{schema}` by the schema "
           "text below, read from `data/olist.db` by `src/schema.py` when the prompt is built.", ""]
    for name in VERS + ["repair"]:
        out += [f"## {name}.txt", "", code((config.PROMPTS / f"{name}.txt").read_text(encoding="utf-8"), "text"), ""]
    v5 = config.PROMPTS / "v5.txt"
    if v5.exists():
        # v5 differs from v3 by one line, so only that line is shown
        extra = [l for l in v5.read_text(encoding="utf-8").splitlines()
                 if l not in (config.PROMPTS / "v3.txt").read_text(encoding="utf-8").splitlines()]
        out += ["## v5.txt (post-hoc, exploratory)", "", "Identical to v3.txt except for this added business rule "
                "(run only on the test2 split):", "", code("\n".join(extra), "text"), ""]
    out += ["## Schema text for v1 (`names_only()`)", "", code(names_only(), "text"), "",
            "## Schema text for v2–v4 (`create_sql()`)", "", code(create_sql()), ""]
    return "\n".join(out)


def reproducibility_md():
    pk = []
    for p in ["groq", "pandas", "streamlit", "python-dotenv", "numpy"]:
        try:
            pk.append(f"{p} {metadata.version(p)}")
        except metadata.PackageNotFoundError:
            pk.append(f"{p} (not installed)")
    calls = pd.read_csv(config.CALLS_CSV) if config.CALLS_CSV.exists() else pd.DataFrame()
    live = calls[(calls["cached"].astype(str) == "False") & (calls["status"] == "ok")] if len(calls) else calls
    n_cache = len(list(config.CACHE.glob("*.json"))) if config.CACHE.exists() else 0
    return "\n".join([
        "# Reproducibility", "",
        "## Model and settings (from `src/config.py`)", "",
        md_table(pd.DataFrame([{"setting": k, "value": v} for k, v in
                               [("provider", "Groq"), ("model", config.MODEL), ("temperature", config.TEMPERATURE),
                                ("top_p", config.TOP_P), ("max_completion_tokens", config.MAX_COMPLETION_TOKENS),
                                ("reasoning_effort", config.REASONING_EFFORT), ("row limit", config.ROW_LIMIT),
                                ("SQL time limit (s)", config.SQL_TIMEOUT), ("retries on rate limit", config.MAX_RETRIES)]])),
        "", f"Python packages used for these files: {', '.join(pk)}.", "",
        "## Cache", "",
        f"- `logs/cache/` holds {n_cache} model replies, one JSON file per call. The file name is the SHA-256 of "
        "(model, settings, prompt text, repeat number), so the same prompt with the same settings always reads the "
        "same reply, and a different model never mixes in.",
        f"- `logs/calls.csv` logs every call, cached or not ({len(live)} live API calls, "
        f"{int(pd.to_numeric(live['total_tokens'], errors='coerce').sum()) if len(live) else 0:,} tokens so far).",
        "- `--offline` reads only from the cache and never calls the API. Every result in `logs/` can be re-scored this "
        "way, e.g. after a gold query is fixed.",
        "- Repeats at temperature 0 are separate cache entries (the repeat number is in the key), so each repeat is a "
        "real separate call.", "",
        "## Exact commands", "",
        code("""# setup (Windows PowerShell)
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then put your own GROQ_API_KEY in .env (never commit it)

# 1. database and data checks (put the 9 Olist CSVs in data/raw/ first)
python src/load_db.py
python src/data_checks.py
python eval/check_gold.py

# 2. offline tests (no API)
python tests/test_validate.py
python tests/test_pipeline.py
python tests/test_score.py

# 3. dev split, all versions (live, or add --offline to use the cache)
python eval/run_eval.py --version v0 --split dev
python eval/run_eval.py --version v1 --split dev
python eval/run_eval.py --version v2 --split dev
python eval/run_eval.py --version v3 --split dev
python eval/run_eval.py --version v4 --split dev

# 4. held-out test split, 3 repeats (only after the prompts are frozen)
python eval/run_eval.py --version v0 --split test --repeats 3 --allow-test
python eval/run_eval.py --version v1 --split test --repeats 3 --allow-test
python eval/run_eval.py --version v2 --split test --repeats 3 --allow-test
python eval/run_eval.py --version v3 --split test --repeats 3 --allow-test
python eval/run_eval.py --version v4 --split test --repeats 3 --allow-test

# 5. second model (robustness)
python eval/run_eval.py --version v1 --split test --allow-test --model openai/gpt-oss-20b
python eval/run_eval.py --version v2 --split test --allow-test --model openai/gpt-oss-20b
python eval/run_eval.py --version v3 --split test --allow-test --model openai/gpt-oss-20b
python eval/run_eval.py --version v4 --split test --allow-test --model openai/gpt-oss-20b

# 6. v0 with the lenient extractor (cache only)
python eval/run_eval.py --version v0 --split dev --offline --lenient
python eval/run_eval.py --version v0 --split test --repeats 3 --allow-test --offline --lenient

# 6b. post-hoc v5 check on the 20 new test2 questions (never on the test split)
python eval/check_gold.py --set test2
python eval/run_eval.py --version v2 --split test2 --allow-test
python eval/run_eval.py --version v3 --split test2 --allow-test
python eval/run_eval.py --version v5 --split test2 --allow-test
python eval/stats.py --split test2

# 7. red team
python eval/run_redteam.py --set all --payload-only     # no API
python eval/run_redteam.py --set all --version v4       # full pipeline

# 8. statistics, error analysis, review sheets, report assets
python eval/stats.py --split dev
python eval/stats.py --split test
python eval/significance.py --split test
python eval/error_report.py --split test                # then fill the labels, then:
python eval/error_report.py --split test --apply
python eval/reversal.py                                 # dev vs test reversal (v1 vs v2)
python eval/make_review_sheets.py
python eval/read_review.py
python eval/make_usability_sheet.py --version v4        # then raters fill it, then:
python eval/read_usability.py
python eval/make_report_assets.py
python eval/export_assets.py                            # figures, results.json, per-question csv

# 9. app
streamlit run app/app.py""", "powershell"), ""])


def abstract_md():
    t = CI["test"]
    head = []
    for v in VERS:
        head.append(f"{v} {acc('test', v)} (strict {sacc('test', v)})")
    m2 = sorted(set(t["model"]) - {config.MODEL}) if len(t) else []
    m2_line = "; ".join(f"{v} {acc('test', v, m2[0])}" for v in VERS[1:]) if m2 else P
    sig = config.LOGS / "significance_test.csv"
    s = pd.read_csv(sig) if sig.exists() else pd.DataFrame()
    have = set(s["version_a"]) | set(s["version_b"]) if len(s) else set()
    if len(s) and {"v2", "v3", "v4"} <= have:
        sig_line = "; ".join(f"{r.version_a} vs {r.version_b}: Holm p = {r.p_holm}" for r in s.itertuples())
    else:
        sig_line = P + " (needs the v2–v4 test runs)"
    rt = redteam_latest()
    rt_line = P if rt is None else (f"{(rt['pipeline_blocked'].astype(str) == 'True').sum()} of {len(rt)} attacks stopped "
                                    f"by the full pipeline (prompt v4); table row counts unchanged: {rt['tables_unchanged'].iloc[0]}")
    ut = unknown_tables()
    dev_line = "; ".join(f"{v} {acc('dev', v)}" for v in VERS)
    lines = [
        "# SchemaSense: abstract", "",
        "_Numbers come from `logs/` via `eval/make_report_assets.py`. PENDING = not run yet. All numbers are "
        "provisional until the team verifies the gold queries._", "",
        "**Problem.** Business users want answers from a database without writing SQL. A language model can write the "
        "SQL, but it can invent tables, misread the business meaning (e.g. count orders instead of people), double-count "
        "after joins, answer questions the data cannot answer, or be talked into writing to the database.", "",
        "**Approach.** One plain-English question becomes one read-only SQLite query over the Olist Brazilian "
        f"e-commerce database (9 tables, ~1.5 million rows). The model is {config.MODEL} on Groq at temperature 0. "
        "Only the prompt changes between five versions: v0 question only; v1 names + output rules; v2 real CREATE TABLE "
        "+ join keys + business rules; v3 v2 + 5 worked examples; v4 v3 + a repair loop that sends SQLite errors back "
        "(max 2). Every answer passes a validator (one SELECT/WITH, no comments, known tables) and a read-only "
        "connection (mode=ro, query_only, read-only authorizer, row and time limits).", "",
        "**Evaluation.** 60 questions in 6 tiers (simple, join, aggregation, fan-out, ambiguous, unanswerable/unsafe), "
        "split 20 dev / 40 held-out test. Execution accuracy compares the rows of the generated and gold queries. "
        "The test split was run 3 times per version; 95% intervals are bootstrapped over questions (n = 40).", "",
        f"**Results (test, execution accuracy [95% CI], strict accuracy in brackets).** {'; '.join(head)}. "
        + GAP_SENTENCE, "",
        f"Dev (n = 20): {dev_line}.", "",
        f"v0's strict-format score includes format failures (prose and comments around the SQL). With a lenient extractor it scores "
        f"{acc('test', 'v0 (lenient extractor)')} on test"
        + (f"; {ut[0]} of {ut[1]} of its answers then fail on an unknown table, so the real gap is schema knowledge."
           if ut != P else "."), "",
        f"Second model, openai/gpt-oss-20b, 1 run: {m2_line}.", "",
        f"Paired McNemar tests: {sig_line}.", "",
        "**Post-hoc, exploratory.** v5 = v3 + one rule (do not approximate missing data; refuse instead), written "
        "after the test results were seen, so it is only run on 20 new questions (test2), never on the test split. "
        f"test2 execution accuracy: v2 {acc('test2', 'v2 (test2, post-hoc, exploratory)')}; "
        f"v3 {acc('test2', 'v3 (test2, post-hoc, exploratory)')}; v5 {acc('test2', 'v5 (test2, post-hoc, exploratory)')}.", "",
        f"Red team: {rt_line}.", "",
        "**Limitations.** 40 test questions (wide intervals); repeats at temperature 0 are not independent; gold queries "
        "and two business-rule assumptions are not yet verified by the team; error labels are drafts; one database, "
        "English only. See `docs/limitations.md`.", "",
    ]
    return "\n".join(lines)


def redteam_latest():
    folder = config.LOGS / "redteam"
    fs = sorted(folder.glob("redteam_all_v4_*.csv")) if folder.exists() else []
    return pd.read_csv(fs[-1]) if fs else None


def txt(v):
    return "no explanation in the reply" if v is None or str(v) in ("", "nan") else str(v)


def first_rows(sql, n=3):
    r = run(sql)
    return r["df"].head(n).to_string(index=False) if r["ok"] else r["error"]


def demo_md():
    d = latest(RES, "dev")

    def get(v, qid):
        return d[(d["version"] == v) & (d["id"] == qid)].iloc[0]

    s01, f01v1, f01v4 = get("v4", "S01"), get("v1", "F01"), get("v4", "F01")
    m08, r01 = get("v4", "M08"), get("v4", "R01")
    j01v0, j01v4 = get("v0", "J01"), get("v4", "J01")
    attack = "SELECT COUNT(*) FROM orders; DROP TABLE orders"
    st, why, _ = check_and_run(attack)
    rt = redteam_latest()

    shots = [
        ("0:00–0:10", "Title + one line", "Open the app (dark theme). Say: \"SchemaSense turns a business question into "
         "one read-only SQL query on the Olist database.\" Point at the sidebar: prompt version v4, Offline demo mode ON."),
        ("0:10–0:30", "1. Normal question", f"Ask tab → sample \"{s01['question']}\" (v4). Show the step badges and "
         f"the result. Status `{s01['status']}`, passes the gold check."),
        ("0:30–0:55", "2. Fan-out question", f"Compare tab, question F01: \"{f01v1['question']}\". v1 → "
         f"{'pass' if str(f01v1['pass']).lower() == 'true' else 'fail'}, v4 → "
         f"{'pass' if str(f01v4['pass']).lower() == 'true' else 'fail'}. Say: v1 joins items and payments together "
         "and double-counts; v2's business rule makes it sum per order first."),
        ("0:55–1:10", "3. Ambiguous question", f"Ask \"{m08['question']}\" (v4). City or state? The test set accepts "
         f"both (gold = state SP, alt = city 'sao paulo'); v4's answer matched `{m08['matched']}`. Read out its "
         f"explanation: \"{txt(m08['explanation'])}\""),
        ("1:10–1:25", "4. Refusal", f"Ask \"{r01['question']}\" (v4). Status `{r01['status']}` "
         f"({txt(r01['explanation'])}). Say: the data has no product names, so the right answer is to refuse."),
        ("1:25–1:45", "5. Blocked attack", f"Below any answer, paste into \"Edit and re-run the SQL\": `{attack}`. "
         f"Status `{st}`: \"{why}\". Then open the Safety tab and show the red-team table"
         + (f" ({(rt['pipeline_blocked'].astype(str) == 'True').sum()} of {len(rt)} stopped)." if rt is not None
            else " (full model run PENDING; payload-only results shown).")),
        ("1:45–2:10", "6. v0 vs v4", f"Compare tab, question J01: \"{j01v0['question']}\". v0 status "
         f"`{j01v0['status']}` ({j01v0['error']}); v4 status `{j01v4['status']}`, "
         f"{'pass' if str(j01v4['pass']).lower() == 'true' else 'fail'}. Say: without the schema the model invents "
         "names."),
        ("2:10–2:25", "Close", "Results dashboard: dev bars v0→v4 and the test numbers with intervals. Say the gold "
         "set is still being verified by the team."),
    ]
    out = ["# Demo script (about 2 min 25 s)", "",
           "_All outputs below are real: they come from the dev results in `logs/` and the cached replies, so the demo "
           "works with **Offline demo mode** on and no API key. Generated by `eval/make_report_assets.py`._", "",
           "Before recording: `streamlit run app/app.py`, sidebar → prompt version v4, Offline demo mode ON.", "",
           "## Shot list", "", md_table(pd.DataFrame(shots, columns=["time", "shot", "what to do and say"])), "",
           "## Real outputs to expect", "",
           f"**1. {s01['question']}** (v4)", code(s01["generated_sql"]), "Result:", code(first_rows(s01["generated_sql"]), "text"), "",
           f"**2. {f01v1['question']}**", "v1 (fails):", code(f01v1["generated_sql"]), "Result:",
           code(first_rows(f01v1["generated_sql"]), "text"), "v4 (passes):", code(f01v4["generated_sql"]), "Result:",
           code(first_rows(f01v4["generated_sql"]), "text"), "",
           f"**3. {m08['question']}** (v4, matched `{m08['matched']}`)", code(m08["generated_sql"]), "Result:",
           code(first_rows(m08["generated_sql"]), "text"), "",
           f"**3b. v4 explanation:** {txt(m08['explanation'])}", "",
           f"**4. {r01['question']}** (v4): `{r01['status']}`. Explanation: {txt(r01['explanation'])}", "",
           f"**5. Edited SQL** `{attack}` → `{st}`: {why}", "",
           f"**6. {j01v0['question']}**", f"v0 (`{j01v0['status']}`: {j01v0['error']}):", code(j01v0["generated_sql"]),
           f"v4 (`{j01v4['status']}`):", code(j01v4["generated_sql"]), "Result:", code(first_rows(j01v4["generated_sql"]), "text"), ""]
    return "\n".join(out)


def results_table():
    rows = []
    m2 = "openai/gpt-oss-20b"
    for v in VERS:
        rows.append({"version": v, "dev exec. (n=20, 1 run)": acc("dev", v), "dev strict": sacc("dev", v),
                     "test exec. (n=40, 3 repeats)": acc("test", v), "test strict": sacc("test", v),
                     "test exec., gpt-oss-20b (1 run)": acc("test", v, m2) if v != "v0" else "not run",
                     "test strict, gpt-oss-20b": sacc("test", v, m2) if v != "v0" else "not run"})
    lv = "v0 (lenient extractor)"
    rows.append({"version": "v0 lenient extractor", "dev exec. (n=20, 1 run)": acc("dev", lv), "dev strict": sacc("dev", lv),
                 "test exec. (n=40, 3 repeats)": acc("test", lv), "test strict": sacc("test", lv),
                 "test exec., gpt-oss-20b (1 run)": "–", "test strict, gpt-oss-20b": "–"})
    t2 = pd.DataFrame([{"version": f"{v} (post-hoc, exploratory)", "test2 exec. (n=20)": acc("test2", f"{v} (test2, post-hoc, exploratory)"),
                        "test2 strict": sacc("test2", f"{v} (test2, post-hoc, exploratory)")} for v in ["v2", "v3", "v5"]])
    return (md_table(pd.DataFrame(rows)) + "\n\n" + GAP_SENTENCE + "\n\nPost-hoc check on 20 new questions (test2), "
            "v5 = v3 + \"do not approximate missing data\". Written after the test results were seen, so it is "
            "exploratory and not part of the main comparison:\n\n" + md_table(t2))


def fill_readme():
    p = ROOT / "README.md"
    s = p.read_text(encoding="utf-8")
    a, b = "<!-- results:start -->", "<!-- results:end -->"
    if a in s and b in s:
        s = s[:s.index(a) + len(a)] + "\n" + results_table() + "\n" + s[s.index(b):]
        p.write_text(s, encoding="utf-8")
        print("updated README.md results table")


def write_all():
    OUT.mkdir(parents=True, exist_ok=True)
    fill_readme()
    files = {
        OUT / "prompt_diff.md": "# Prompt diff v0 → v4\n\n_Accuracy = execution accuracy with a 95% bootstrap interval "
                                "over questions. Dev n = 20 (1 run), test n = 40 (3 repeats). Label counts come from "
                                "Claude's draft dev labels (logs/error_labels_dev.csv)._\n\n" + md_table(prompt_diff()) + "\n",
        OUT / "v0_baseline.md": "# v0 baseline: strict format vs lenient extractor\n\n_The lenient extractor (`src/pipeline.py` "
                                "`lenient_parse`) takes the first SQL block, removes comments and prose and keeps the first "
                                "statement. It re-scores cached answers only (`--offline --lenient`, results in logs/lenient/). "
                                "v1–v4 are scored as before._\n\n" + md_table(v0_baseline()) + "\n",
        OUT / "failure_examples.md": failure_examples_md() + "\n",
        OUT / "prompts_appendix.md": prompts_appendix_md() + "\n",
        OUT / "reproducibility.md": reproducibility_md() + "\n",
        OUT / "abstract.md": abstract_md() + "\n",
        ROOT / "docs" / "demo_script.md": demo_md() + "\n",
    }
    for p, text in files.items():
        p.write_text(text, encoding="utf-8")
        print("wrote", p.relative_to(ROOT))


if __name__ == "__main__":
    write_all()
