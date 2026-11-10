# runs one prompt version on one split of eval/test_set.json and writes a results csv to logs/
# examples:
#   python eval/run_eval.py --version v2 --split dev
#   python eval/run_eval.py --version v4 --split dev --repeats 3
#   python eval/run_eval.py --version v1 --split dev --ids S01,J04 --offline
#   python eval/run_eval.py --version v4 --split test --allow-test --model <other groq model>   (robustness check)
#   python eval/run_eval.py --version v0 --split test --repeats 3 --allow-test --offline --lenient
#       (v0 baseline with the forgiving sql extractor, from the cache only. saved in logs/lenient/ so it never
#        replaces the normal results that the dashboard and significance test use)
# the held-out test split is locked behind --allow-test so nobody runs it by accident

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import config
from build_prompt import VERSIONS
from pipeline import run_question
from score import print_summary, score_item

TEST_SET = ROOT / "eval" / "test_set.json"
TEST2_SET = ROOT / "eval" / "test2_set.json"  # 20 new questions for the post-hoc v5 check -> logs/test2/

COLUMNS = [
    "run_id", "time", "version", "split", "repeat", "id", "tier", "unsafe", "question",
    "expect_refuse", "refuse_ok", "generated_sql", "status", "error", "explanation",
    "retries", "tokens", "latency_s", "cached", "system_refused", "pass", "strict_pass",
    "matched", "score_note", "error_category", "model", "temperature", "top_p",
    "max_completion_tokens", "reasoning_effort",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, choices=VERSIONS)
    ap.add_argument("--split", default="dev", choices=["dev", "test", "test2"])
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--ids", default="", help="comma separated ids, e.g. S01,J04")
    ap.add_argument("--offline", action="store_true", help="use cached answers only")
    ap.add_argument("--allow-test", action="store_true", help="needed to run the held-out test split")
    ap.add_argument("--lenient", action="store_true",
                    help="forgiving sql extractor (fences, prose and comments removed). needs --offline")
    ap.add_argument("--model", default=config.MODEL,
                    help="other Groq model for the robustness check (all other settings stay the same)")
    a = ap.parse_args()

    # second-model check: only the model changes. it is part of the cache key, so answers never mix
    main_model = config.MODEL
    config.MODEL = a.model

    if a.lenient and not a.offline:
        sys.exit("--lenient is only for re-scoring cached answers. Add --offline.")
    if a.version == "v5" and a.split == "test":
        sys.exit("v5 was written after the test split was seen, so it is only run on test2 (eval/test2_set.json).")
    if a.split in ("test", "test2") and not a.allow_test:
        sys.exit("The test split is held out. Add --allow-test only when the team has agreed to run it.")
    if not a.offline and not os.getenv("GROQ_API_KEY"):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        if not os.getenv("GROQ_API_KEY"):
            sys.exit("GROQ_API_KEY is not set (see .env.example). Use --offline to run from the cache only.")

    src = TEST2_SET if a.split == "test2" else TEST_SET
    items = [it for it in json.loads(src.read_text(encoding="utf-8")) if it["split"] == a.split]
    if a.ids:
        want = set(a.ids.split(","))
        items = [it for it in items if it["id"] in want]
    if not items:
        sys.exit("no questions selected")

    # the default model keeps the short name; another model gets its name in the file name too
    tag = "" if a.model == main_model else re.sub(r"[^A-Za-z0-9.-]+", "-", a.model) + "_"
    tag += "lenient_" if a.lenient else ""
    run_id = f"{a.version}_{a.split}_{tag}{datetime.now():%Y%m%d-%H%M%S}"
    rows = []
    for rep in range(1, a.repeats + 1):
        for n, it in enumerate(items, 1):
            o = run_question(it["question"], a.version, qid=it["id"], repeat=rep, offline=a.offline,
                             lenient=a.lenient)
            s = score_item(it, o)
            print(f"[rep {rep}] {n:2d}/{len(items)} {it['id']} {o['status']:9s} "
                  f"{'PASS' if s['pass'] else 'fail'}{'  (cached)' if o['cached'] else ''}")
            rows.append({
                "run_id": run_id, "time": datetime.now().isoformat(timespec="seconds"),
                "version": a.version, "split": a.split, "repeat": rep, "id": it["id"], "tier": it["tier"],
                "unsafe": it.get("unsafe", False), "question": it["question"],
                "expect_refuse": it["expected"] == "REFUSE", "refuse_ok": it.get("refuse_ok", False),
                "generated_sql": o["sql"], "status": o["status"], "error": o["error"],
                "explanation": o["explanation"], "retries": o["retries"], "tokens": o["tokens"],
                "latency_s": o["latency_s"], "cached": o["cached"], "system_refused": s["system_refused"],
                "pass": s["pass"], "strict_pass": s["strict_pass"], "matched": s["matched"],
                "score_note": s["note"], "error_category": "",  # filled by hand during error analysis
                "model": config.MODEL, **config.SETTINGS,
                **({"extractor": "lenient"} if a.lenient else {}),
            })

    if all(r["status"] == "llm_error" for r in rows):
        sys.exit("Every call failed (no API key, or not in the cache in --offline mode). No results file written.")

    # lenient re-scores and the post-hoc test2 runs get their own folders, so they never mix with the main results
    folder = config.LOGS / "lenient" if a.lenient else config.LOGS / "test2" if a.split == "test2" else config.LOGS
    folder.mkdir(exist_ok=True)
    out = folder / f"results_{run_id}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS + (["extractor"] if a.lenient else []))
        w.writeheader()
        w.writerows(rows)
    print(f"\nsaved {out}\n")
    print_summary(pd.read_csv(out))


if __name__ == "__main__":
    main()
