# v0 baseline: strict format vs lenient extractor

_The lenient extractor (`src/pipeline.py` `lenient_parse`) takes the first SQL block, removes comments and prose and keeps the first statement. It re-scores cached answers only (`--offline --lenient`, results in logs/lenient/). v1–v4 are scored as before._

| split | extractor | answers | execution acc. [95% CI] | valid SQL | statuses |
|---|---|---|---|---|---|
| dev | strict format (as scored) | 20 | 15.0% [0.0–30.0] | 6.2% | blocked 15, error 2, invalid 2, ok 1 |
| dev | lenient extractor | 20 | 15.0% [0.0–30.0] | 12.5% | error 10, invalid 6, ok 2, blocked 2 |
| test | strict format (as scored) | 120 | 7.5% [0.0–17.5] | 2.9% | blocked 103, invalid 10, error 4, ok 3 |
| test | lenient extractor | 120 | 10.0% [2.5–20.0] | 8.8% | invalid 73, error 29, ok 12, blocked 6 |
