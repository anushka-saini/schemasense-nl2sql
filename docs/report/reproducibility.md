# Reproducibility

## Model and settings (from `src/config.py`)

| setting | value |
|---|---|
| provider | Groq |
| model | openai/gpt-oss-120b |
| temperature | 0 |
| top_p | 1 |
| max_completion_tokens | 2048 |
| reasoning_effort | medium |
| row limit | 200 |
| SQL time limit (s) | 10 |
| retries on rate limit | 5 |

Python packages used for these files: groq 1.7.0, pandas 3.1.0, streamlit 1.66.1, python-dotenv 1.2.4, numpy 2.5.4.

## Cache

- `logs/cache/` holds 762 model replies, one JSON file per call. The file name is the SHA-256 of (model, settings, prompt text, repeat number), so the same prompt with the same settings always reads the same reply, and a different model never mixes in.
- `logs/calls.csv` logs every call, cached or not (762 live API calls, 914,124 tokens so far).
- `--offline` reads only from the cache and never calls the API. Every result in `logs/` can be re-scored this way, e.g. after a gold query is fixed.
- Repeats at temperature 0 are separate cache entries (the repeat number is in the key), so each repeat is a real separate call.

## Exact commands

```powershell
# setup (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
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
streamlit run app/app.py
```

