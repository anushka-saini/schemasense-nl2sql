# SchemaSense: safe natural-language-to-SQL on Olist

A prompt engineering study for the Prompt Engineering course, Bennett University (3-person mini-project).
A user asks a question in plain English, an LLM writes one SQLite `SELECT` query over the
[Olist Brazilian e-commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce), and the query runs on a read-only copy of the database.

The graded part is the **prompt experiments**: five prompt versions (v0 to v4), each adding one idea, scored on a 60-question test set with a held-out split, plus error analysis. The Streamlit app is the demo layer.

- **Model:** `openai/gpt-oss-120b` on Groq, temperature 0, top_p 1, max_completion_tokens 2048, reasoning_effort medium (all in `src/config.py`)
- **Safety:** prompt rules, a SQL validator and a read-only database connection ([docs/design.md](docs/design.md))

> **Status: provisional.** The 60 gold queries and the error labels were drafted with Claude's help and are **not verified** by the team yet (`verified: false` in `eval/test_set.json`). Two business-rule definitions are assumptions. Review sheets are in `review/`.

## Setup

Needs Python 3.12 (Windows, macOS or Linux).

```bash
python -m venv .venv
# Windows PowerShell:  .\.venv\Scripts\Activate.ps1
# macOS / Linux:       source .venv/bin/activate
pip install -r requirements.txt
```

1. **Data.** Download the Olist CSVs yourself and put all 9 files in `data/raw/` (e.g. `olist_orders_dataset.csv`, `product_category_name_translation.csv`). Then build the database:
   ```bash
   python src/load_db.py
   ```
   This creates `data/olist.db` and prints the row counts. The database and the CSVs are not in git.
2. **API key.** Copy `.env.example` to `.env` and put your own Groq key in it (`GROQ_API_KEY=...`). The key is only read from the environment and is never stored in code or committed. Without a key, everything works from cached answers (`--offline`, or the app's offline toggle).

## Folder structure

```
data/raw/            Olist CSVs (you add these, not in git)
data/olist.db        SQLite database built by src/load_db.py (not in git)
prompts/             v0.txt ... v4.txt, repair.txt, v5.txt (post-hoc), CHANGELOG.md (all prompt versions in one place)
src/
  config.py          model settings and paths (one place for all settings)
  load_db.py         CSVs -> data/olist.db
  schema.py          reads the real schema from the database
  data_checks.py     the queries behind docs/data_notes.md
  llm.py             ask(prompt, tag): Groq call + cache + call log + retry
  build_prompt.py    fills {schema} and {question}
  validate.py        single-SELECT validator, returns (ok, reason)
  run_sql.py         read-only SQLite runner (200 rows, 10 s)
  pipeline.py        one question end to end, incl. the v4 repair loop and the lenient extractor (analysis only)
eval/
  test_set.json      60 questions, 6 tiers, 20 dev / 40 held-out test (gold SQL: draft, not verified)
  test2_set.json     20 new questions for the post-hoc v5 check only (written by make_test2.py, not verified)
  check_gold.py      runs every gold query, lists broken ones and overlaps with the v3 examples
  score.py           execution accuracy and the other metrics
  run_eval.py        runs a version on a split -> logs/results_*.csv (--lenient -> logs/lenient/)
  stats.py           95% confidence intervals (question = unit) -> logs/accuracy_ci_*.csv, docs/limitations.md
  significance.py    paired exact McNemar, Holm-corrected -> logs/significance_*.csv
  error_report.py    failure labelling sheet -> logs/error_labels_*.csv
  run_redteam.py     docs/redteam.json through each safety layer -> logs/redteam/
  make_review_sheets.py / read_review.py   human review sheets in review/
  make_report_assets.py                    docs/report/*.md and docs/demo_script.md from the logs
  reversal.py        dev-vs-test reversal analysis (v1 vs v2) -> docs/report/dev_test_reversal.md
  export_assets.py   docs/report/results.json, figures (svg + png), logs/per_question_test.csv
  make_usability_sheet.py / read_usability.py   human usefulness ratings in review/
tests/               validator, pipeline and scorer tests (no API key needed)
app/                 Streamlit demo (app.py + helpers), theme in .streamlit/
review/              sheets for the team: gold_review(_test2).csv, labels_review(_test).csv, usability_sheet.csv (+ .html to read)
logs/                the evidence: call log, LLM cache, results, red-team runs, test outputs
docs/                design, data notes, error taxonomy, limitations, responsible AI (draft), report assets
```

## Prompt versions

Full text: `prompts/` and [docs/report/prompts_appendix.md](docs/report/prompts_appendix.md). Why each change was made: [prompts/CHANGELOG.md](prompts/CHANGELOG.md). Diff with accuracy: [docs/report/prompt_diff.md](docs/report/prompt_diff.md).

| version | adds | schema given | examples | repair loop |
|---|---|---|---|---|
| v0 | control: question only | none | none | no |
| v1 | SELECT-only + CANNOT_ANSWER + answer format | table and column names | none | no |
| v2 | join keys + business rules | real CREATE TABLE | none | no |
| v3 | worked examples | real CREATE TABLE | 5 | no |
| v4 | repair with the exact error | real CREATE TABLE | 5 | yes, max 2 |
| v5 | *post-hoc, exploratory:* v3 + "do not approximate missing data" (test2 only) | real CREATE TABLE | 5 | no |

## Test data

`eval/test_set.json` has 60 questions in 6 tiers of 10: simple, join, aggregation, multi-step/fan-out, ambiguous, unanswerable/unsafe. 20 are the **dev** split (used to check the prompts) and 40 are the **held-out test** split, which needs `--allow-test` and was run only after the prompts were frozen. There was no training; "dev" plays the role of a validation set. The 5 v3 examples are separate questions, and `eval/check_gold.py` checks that they do not overlap with the test set.

`eval/test2_set.json` is a third, separate split of 20 new questions (`test2`). It exists only for the post-hoc v5 check: v5 was written after the test results were seen, so it is never run on the test split.

## Tests (no API key)

```bash
python tests/test_validate.py    # validator + read-only connection
python tests/test_pipeline.py    # reply parsing, lenient extractor, prompts, v3 examples, repair loop (fake LLM)
python tests/test_score.py       # result comparison and metrics
python eval/check_gold.py        # every gold query runs and returns rows
```

## Re-running everything

Every command, in order, is in [docs/report/reproducibility.md](docs/report/reproducibility.md). The short version:

```bash
# evaluation (add --offline to use only the cache; the test split needs --allow-test)
python eval/run_eval.py --version v2 --split dev
python eval/run_eval.py --version v2 --split test --repeats 3 --allow-test
# analysis (no API)
python eval/stats.py --split test
python eval/significance.py --split test
python eval/error_report.py --split test
python eval/run_redteam.py --set all --payload-only
python eval/make_review_sheets.py
python eval/make_report_assets.py
```

## App

```bash
streamlit run app/app.py
```

Open http://localhost:8501. Tabs: **Ask** (step-by-step pipeline view, edit and re-run the SQL), **Compare versions**, **Results dashboard** (reads `logs/results_*.csv`, with 95% intervals), **Safety** (newest file in `logs/redteam/`), **Schema and data**, **About**. Turn on *Offline demo mode* in the sidebar to use only cached answers. The demo script is in [docs/demo_script.md](docs/demo_script.md).

## Results

Generated from `logs/` by `python eval/make_report_assets.py`. Execution accuracy with a 95% bootstrap interval over questions. PENDING = not run yet. Provisional until the gold set is verified.

<!-- results:start -->
| version | dev exec. (n=20, 1 run) | dev strict | test exec. (n=40, 3 repeats) | test strict | test exec., gpt-oss-20b (1 run) | test strict, gpt-oss-20b |
|---|---|---|---|---|---|---|
| v0 | 15.0% [0.0–30.0] | 15.0% [0.0–30.0] | 7.5% [0.0–17.5] | 7.5% [0.0–17.5] | not run | not run |
| v1 | 55.0% [35.0–75.0] | 50.0% [30.0–70.0] | 81.7% [69.2–92.5] | 66.7% [52.5–80.8] | 57.5% [42.5–72.5] | 45.0% [30.0–60.0] |
| v2 | 90.0% [75.0–100.0] | 80.0% [60.0–95.0] | 80.8% [69.2–90.8] | 61.7% [47.5–75.8] | 82.5% [70.0–92.5] | 62.5% [47.5–77.5] |
| v3 | 90.0% [75.0–100.0] | 75.0% [55.0–90.0] | 88.3% [78.3–96.7] | 62.5% [47.5–76.7] | 92.5% [82.5–100.0] | 67.5% [52.5–82.5] |
| v4 | 90.0% [75.0–100.0] | 75.0% [55.0–90.0] | 88.3% [78.3–96.7] | 62.5% [47.5–76.7] | 95.0% [87.5–100.0] | 70.0% [55.0–82.5] |
| v0 lenient extractor | 15.0% [0.0–30.0] | 15.0% [0.0–30.0] | 10.0% [2.5–20.0] | 10.0% [2.5–20.0] | – | – |

Strict accuracy also requires exactly the gold number of columns; the gap to execution accuracy is answers with the right rows plus extra columns (for example a count next to the name asked for).

Post-hoc check on 20 new questions (test2), v5 = v3 + "do not approximate missing data". Written after the test results were seen, so it is exploratory and not part of the main comparison:

| version | test2 exec. (n=20) | test2 strict |
|---|---|---|
| v2 (post-hoc, exploratory) | 90.0% [75.0–100.0] | 75.0% [55.0–95.0] |
| v3 (post-hoc, exploratory) | 85.0% [70.0–100.0] | 70.0% [50.0–90.0] |
| v5 (post-hoc, exploratory) | 90.0% [75.0–100.0] | 75.0% [55.0–90.0] |
<!-- results:end -->

Figures for the write-up: `docs/report/figures/` (SVG + PNG). Every number: `docs/report/results.json`.

More: [abstract](docs/report/abstract.md), [dev vs test reversal](docs/report/dev_test_reversal.md), [limitations](docs/limitations.md), [failure examples](docs/report/failure_examples.md), [v0 strict vs lenient](docs/report/v0_baseline.md), [responsible AI (draft)](docs/responsible_ai.md).

## Notes

- Gold SQL in `eval/test_set.json` is a first draft. Every item has `verified: false` until a team member checks it (`review/gold_review.csv`, then `python eval/read_review.py`).
- Two business-rule definitions (which orders count as sales, and what "revenue" means) are assumptions, listed in `prompts/CHANGELOG.md`.
- Facts about the data, each checked by a query, are in `docs/data_notes.md`.
- Changes to the validator after a red-team run go in `docs/validator_changelog.md` as v-next.
- Who did what: `docs/contribution_log.md`.
