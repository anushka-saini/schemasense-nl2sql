# SchemaSense: abstract

_Numbers come from `logs/` via `eval/make_report_assets.py`. PENDING = not run yet. All numbers are provisional until the team verifies the gold queries._

**Problem.** Business users want answers from a database without writing SQL. A language model can write the SQL, but it can invent tables, misread the business meaning (e.g. count orders instead of people), double-count after joins, answer questions the data cannot answer, or be talked into writing to the database.

**Approach.** One plain-English question becomes one read-only SQLite query over the Olist Brazilian e-commerce database (9 tables, ~1.5 million rows). The model is openai/gpt-oss-120b on Groq at temperature 0. Only the prompt changes between five versions: v0 question only; v1 names + output rules; v2 real CREATE TABLE + join keys + business rules; v3 v2 + 5 worked examples; v4 v3 + a repair loop that sends SQLite errors back (max 2). Every answer passes a validator (one SELECT/WITH, no comments, known tables) and a read-only connection (mode=ro, query_only, read-only authorizer, row and time limits).

**Evaluation.** 60 questions in 6 tiers (simple, join, aggregation, fan-out, ambiguous, unanswerable/unsafe), split 20 dev / 40 held-out test. Execution accuracy compares the rows of the generated and gold queries. The test split was run 3 times per version; 95% intervals are bootstrapped over questions (n = 40).

**Results (test, execution accuracy [95% CI], strict accuracy in brackets).** v0 7.5% [0.0–17.5] (strict 7.5% [0.0–17.5]); v1 81.7% [69.2–92.5] (strict 66.7% [52.5–80.8]); v2 80.8% [69.2–90.8] (strict 61.7% [47.5–75.8]); v3 88.3% [78.3–96.7] (strict 62.5% [47.5–76.7]); v4 88.3% [78.3–96.7] (strict 62.5% [47.5–76.7]). Strict accuracy also requires exactly the gold number of columns; the gap to execution accuracy is answers with the right rows plus extra columns (for example a count next to the name asked for).

Dev (n = 20): v0 15.0% [0.0–30.0]; v1 55.0% [35.0–75.0]; v2 90.0% [75.0–100.0]; v3 90.0% [75.0–100.0]; v4 90.0% [75.0–100.0].

v0's strict-format score includes format failures (prose and comments around the SQL). With a lenient extractor it scores 10.0% [2.5–20.0] on test; 73 of 120 of its answers then fail on an unknown table, so the real gap is schema knowledge.

Second model, openai/gpt-oss-20b, 1 run: v1 57.5% [42.5–72.5]; v2 82.5% [70.0–92.5]; v3 92.5% [82.5–100.0]; v4 95.0% [87.5–100.0].

Paired McNemar tests: v0 vs v1: Holm p = 0.0; v0 vs v2: Holm p = 0.0; v0 vs v3: Holm p = 0.0; v0 vs v4: Holm p = 0.0; v1 vs v2: Holm p = 1.0; v1 vs v3: Holm p = 1.0; v1 vs v4: Holm p = 1.0; v2 vs v3: Holm p = 1.0; v2 vs v4: Holm p = 1.0; v3 vs v4: Holm p = 1.0.

**Post-hoc, exploratory.** v5 = v3 + one rule (do not approximate missing data; refuse instead), written after the test results were seen, so it is only run on 20 new questions (test2), never on the test split. test2 execution accuracy: v2 90.0% [75.0–100.0]; v3 85.0% [70.0–100.0]; v5 90.0% [75.0–100.0].

Red team: 18 of 18 attacks stopped by the full pipeline (prompt v4); table row counts unchanged: True.

**Limitations.** 40 test questions (wide intervals); repeats at temperature 0 are not independent; gold queries and two business-rule assumptions are not yet verified by the team; error labels are drafts; one database, English only. See `docs/limitations.md`.

