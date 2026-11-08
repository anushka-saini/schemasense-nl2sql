# Error taxonomy

Use the **code** in the `error_category` column of the results CSVs (`logs/results_*.csv`) when labelling failed questions by hand.
The example columns are empty on purpose. Fill them only from real failures in real runs, and give the results file name.

| code | failure type | what it looks like | how to spot it | example question id | example generated SQL | results file |
|---|---|---|---|---|---|---|
| `hallucinated_column` | Hallucinated column or table | Uses a column or table that does not exist (e.g. `status` instead of `order_status`) | status `invalid` (unknown table) or `error` with "no such column" | | | |
| `wrong_join_key` | Wrong join key | Joins on the wrong columns (e.g. `customer_unique_id = customer_id`, or zip prefix to geolocation) | runs, but the result is far off; check the ON clause | | | |
| `fanout_double_count` | Fan-out double count | Sums `price` or `payment_value` after joining two one-to-many tables (items × payments, items × reviews) | totals too big; see `docs/data_notes.md` note 8 | | | |
| `wrong_grain` | Wrong grain | Counts at the wrong level: orders vs items, `customer_id` vs `customer_unique_id`, reviews vs orders | right shape, wrong number | | | |
| `missing_status_filter` | Missing or wrong status filter | Sales or delivery question without `order_status = 'delivered'`, or a filter where none was asked | compare with the business rule in `prompts/v2.txt` | | | |
| `date_handling` | Date handling | Wrong year or month filter, uses `'now'`, compares dates as text in the wrong format, ignores NULL delivery dates | check strftime / julianday parts | | | |
| `language_mismatch` | Language mismatch | Filters an English category on the Portuguese column (or the reverse), or returns Portuguese names when English was asked | 0 rows, or non-English names in the result | | | |
| `guessed_on_ambiguity` | Guessed on ambiguity | Picks one meaning of a vague question that matches neither the gold nor an `alt_sql` | ambiguous tier fails | | | |
| `over_refusal` | Refused an answerable question | CANNOT_ANSWER on a question the schema can answer | status `refused` on a non-REFUSE item | | | |
| `answered_unanswerable` | Answered an unanswerable question | Returns SQL for something the data doesn't have (e.g. product_id for "product name") | REFUSE item with status `ok` or `empty` | | | |
| `format_failure` | Format failure | No SQL could be read from the reply, extra statements, or prose mixed into the SQL | status `invalid` with "no SQL found" | | | |
| `wrong_filter_value` | Filter value in the wrong format | Filters with a value that doesn't match how the data stores it, e.g. `'Rio de Janeiro'` instead of `'RJ'`, `'Sao Paulo'` instead of `'sao paulo'`. Added after the dev run showed this pattern | runs, but returns 0 or wrong rows | | | |
| `other` | Other | Anything not covered above. Describe it in the notes | | | | |

## Count per category

Fill this in after labelling. Leave it empty until then.

| code | dev v1 | dev v2 | dev v3 | dev v4 | test v1 | test v2 | test v3 | test v4 |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |
