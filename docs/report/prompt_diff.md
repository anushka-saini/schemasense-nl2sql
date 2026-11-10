# Prompt diff v0 → v4

_Accuracy = execution accuracy with a 95% bootstrap interval over questions. Dev n = 20 (1 run), test n = 40 (3 repeats). Label counts come from Claude's draft dev labels (logs/error_labels_dev.csv)._

| version | what changed | why | targets | targeted dev labels (prev → this) | dev acc. [95% CI] | test acc. [95% CI] |
|---|---|---|---|---|---|---|
| v0 | Control: the question only, no schema, no rules. | Floor: what the model does when it knows nothing about the database. | – | – | 15.0% [0.0–30.0] | 7.5% [0.0–17.5] |
| v1 | Baseline instructions (one SELECT, never write, CANNOT_ANSWER, SQL then a one-line explanation) + table and column names. | The model needs the real names and a fixed answer format. | `format_failure`, `hallucinated_column` | 17 → 0 | 55.0% [35.0–75.0] | 81.7% [69.2–92.5] |
| v2 | Real CREATE TABLE text + join keys + business rules (unique customers, fan-out, delivered = sales, revenue, English categories, text dates, city/state formats). | Some facts are only visible in the data, not in the names. | `fanout_double_count`, `wrong_grain`, `missing_status_filter`, `language_mismatch`, `wrong_filter_value`, `date_handling`, `wrong_join_key` | 8 → 1 | 90.0% [75.0–100.0] | 80.8% [69.2–90.8] |
| v3 | 5 worked examples in the answer format (unique customers, per-order CTEs, English category, delivery days, a CANNOT_ANSWER case). | Rules say what to do; examples show it. | `wrong_grain`, `fanout_double_count`, `language_mismatch`, `date_handling`, `answered_unanswerable` | 2 → 2 | 90.0% [75.0–100.0] | 88.3% [78.3–96.7] |
| v4 | Same text as v3 + a repair loop: invalid SQL, a SQLite error or 0 rows are sent back with the exact error, at most 2 times. | Many failures are small and SQLite names the problem exactly. | `hallucinated_column`, `wrong_filter_value` | 0 → 0 | 90.0% [75.0–100.0] | 88.3% [78.3–96.7] |
