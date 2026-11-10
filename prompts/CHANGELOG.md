# Prompt changelog

## Iteration log (short)

| date | what happened | prompt files changed? |
|---|---|---|
| 2026-11-08 | v0–v4 and repair.txt written (Phase 3). v4.txt = v3.txt byte for byte | written |
| 2026-11-08 | M05 test question replaced: its gold answer equalled a v3 example result (159.86) | no (test set only) |
| 2026-11-08 | Dev run, all 5 versions. Scorer fix: a validator block counts as a refusal only for unsafe items; dev re-scored from the cache | no (scorer only) |
| 2026-11-08 | Prompts **frozen**, then the held-out test runs | no |
| 2026-11-09 | Lenient extractor added for the v0 baseline analysis only (`--offline --lenient`) | no |
| next (not applied) | Dev showed v2–v4 inventing a profit formula on R02 while v1 refused. Candidate rule for a future v5: "if a needed value is not in the schema, do not approximate it; reply CANNOT_ANSWER". Not added, so the test results stay comparable | – |
| 2026-11-09 | **v5.txt written (post-hoc, exploratory)** = v3.txt + that one rule, added as the last business rule. v0–v4 unchanged. Written after the v1/v2 test results were seen, so v5 is never run on the test split (`run_eval.py` refuses). Then 20 new questions written in `eval/test2_set.json` (split `test2`), after v5 was fixed and without running any model on them | v5.txt added |

## v5: v3 + "do not approximate missing data" (post-hoc, exploratory)
- **Prompt text:** v3.txt plus one business rule: "Do not approximate missing data. If the schema lacks a column or value the question needs, reply exactly: CANNOT_ANSWER. Do not use a different column or a made-up formula instead." No repair loop.
- **Why:** on dev R02 (profit margin) v2–v4 built a profit formula from columns that do not mean profit, while v1 refused.
- **Why only on test2:** the rule was chosen after looking at results, so running it on the held-out test split would make that split no longer held out. test2 has 20 new questions (3 simple, 3 join, 3 aggregation, 3 fan-out, 2 ambiguous, 6 unanswerable), drafted by Claude after v5.txt was written, gold SQL checked by `python eval/check_gold.py --set test2`, all `verified: false`. Results go to `logs/test2/` and every v5 number is labelled "post-hoc, exploratory".

The sections below explain each version in detail.

All versions use the same model settings from `src/config.py` (gpt-oss-120b, temperature 0, top_p 1, max_completion_tokens 2048, reasoning_effort medium).
Each prompt is sent as a single user message. `src/build_prompt.py` fills the `{schema}` and `{question}` slots.

The safety layers (validator plus read-only connection) run for **every** version, so the versions only differ in prompt text, except v4.

| version | adds | schema given | rules | examples | repair loop |
|---|---|---|---|---|---|
| v0 | control | none | none | none | no |
| v1 | baseline instructions + table/column names | names only | SELECT-only, CANNOT_ANSWER | none | no |
| v2 | real CREATE TABLE + business rules | CREATE TABLE | + join keys and business rules | none | no |
| v3 | worked examples | CREATE TABLE | same as v2 | 5 | no |
| v4 | repair loop | CREATE TABLE | same as v2 | 5 | yes, max 2 |

## v0: control
- **Prompt:** "Write a SQLite query that answers this question." plus the question. No schema, no rules.
- **Why:** shows what the model does with no knowledge of the database. We expect made-up table and column names, which the validator or SQLite should catch. This is the floor the other versions are measured against.
- **Note:** v0 does not ask for an explanation or allow CANNOT_ANSWER, so it cannot refuse on purpose.

## v1: zero-shot with table and column names
- **Change from v0:** our baseline wording (SELECT only, never INSERT/UPDATE/DELETE/DROP, reply CANNOT_ANSWER if not answerable, SQL then a one-line explanation), plus a compact `table(col1, col2, ...)` list generated from the real database by `schema.names_only()`.
- **Why:** tests whether knowing the names alone is enough. No types, no join keys, no business meaning.
- The instruction text is our baseline wording, unchanged.

## v2: real CREATE TABLE + business rules
- **Change from v1:** the names list is replaced with the real `CREATE TABLE` text from `sqlite_master` (`schema.create_sql()`). Join keys and business rules taken from `docs/data_notes.md` are added. The instruction paragraph is unchanged; the only header change is "Schema (table(columns)):" to "Schema:".
- **Why:** v1 cannot know facts that are only in the data:
  - customer_id is per order (note 1)
  - order_items × order_payments fan-out doubles totals (note 8)
  - category names are Portuguese (note 11)
  - timestamps are text (note 5)
  - which order_status values exist (note 2)

  Each rule maps to a numbered note in `docs/data_notes.md`.
- **Two changes in one version:** this follows the project plan for v2. If v2 beats v1, we cannot tell how much came from types/CREATE TABLE and how much from the rules. A possible later ablation is v1 + rules only.
- **ASSUMPTIONS (defaults chosen by Claude; the team has not decided yet, please confirm or change):**
  1. Sales, revenue and delivery questions use only `order_status = 'delivered'` unless the question says otherwise.
  2. "Money paid by customers" = `SUM(order_payments.payment_value)`. "Revenue" for products, categories or sellers = `SUM(order_items.price)` (without freight). Payments are not split per item, so `price` is the only per-category measure.

  The gold SQL in the test set must use the same definitions, or v2+ will be marked wrong for following the rules.

## v3: worked examples (few-shot)
- **Change from v2:** 5 worked examples inserted before the question, written in the exact answer format we want (```sql block, then `Explanation:`). Rules and schema are unchanged.
- **Why:** rules state what to do, while examples show it. Each example targets one rule:

| # | example question | teaches | checked result |
|---|---|---|---|
| 1 | Which 3 states have the most customers? | `COUNT(DISTINCT customer_unique_id)` | SP 40,302 / RJ 12,384 / MG 11,259 |
| 2 | For delivered orders, average number of items and average amount paid per order? | aggregate items and payments per order in CTEs, then join (fan-out) | 1.14 items, 159.86 paid |
| 3 | How many products are in the garden tools category? | English category via translation table | 753 |
| 4 | Average delivery time in days for delivered orders purchased in March 2018? | `julianday`, NULL delivered date, `strftime` month filter | 16.3 days |
| 5 | What is the email address of the customer who spent the most? | reply `CANNOT_ANSWER` (no email column) | refusal |

- **Checked results:** the SQL of examples 1 to 4 was run on `olist.db` by `tests/test_pipeline.py` (section 3, output in `logs/test_pipeline.txt`). The numbers above come from that run.
- **Rule for Phase 4:** these 5 questions, and close paraphrases of them, must **not** appear in the test set, or v3/v4 scores would be inflated.

## v4: v3 + repair loop
- **Prompt text:** byte-for-byte identical to v3 (`v4.txt` = `v3.txt`). The only change is in `src/pipeline.py`.
- **Loop:** after the first answer, the SQL goes through the validator and the read-only connection. A repair prompt (`prompts/repair.txt`) is sent, up to **2 times**, if:
  - the validator rejects it for a non-safety reason (e.g. unknown table)
  - there is no SQL in the reply
  - SQLite returns an error
  - the result has 0 rows

  The repair prompt is the full original v4 prompt + the previous SQL + the exact error message.
- **Not repaired:** unsafe queries (forbidden keyword, stacked statements, comments, system tables) stay `blocked`. We never ask the model to "fix" an attack into something runnable. CANNOT_ANSWER is final too.
- **Why:** many failures are small (a wrong column name, a wrong value case) and the SQLite error message says exactly what is wrong. This tests how much self-correction with real feedback adds on top of v3.
- **Side effect:** because the prompt text equals v3, v4's first attempt has the same cache key as v3's answer and reuses it. So the v3 to v4 difference measures only the repair loop. Repair calls are logged with tags `v4-r1` and `v4-r2` in `logs/calls.csv`.
- **Risk to watch:** repairing on empty results can push the model to loosen filters until something comes back, even when empty was the right answer. Check this in the error analysis.
