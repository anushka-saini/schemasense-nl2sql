# runs the attacks in docs/redteam.json and records where each one was stopped
# two checks per attack:
#   1. pipeline: the attack text goes through the full pipeline (prompt -> llm -> validator -> read-only db)
#      layer = prompt (model refused or wrote only a harmless read), validator, or read-only connection
#   2. payload: the attacker's sql goes straight to the validator, and then straight to the
#      read-only connection with the validator skipped. this needs no api and shows each layer alone
# also checks that every table still has the same row count afterwards
# run: python eval/run_redteam.py [--version v4] [--offline]
# output: logs/redteam_results.csv

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config
from pipeline import run_question
from run_sql import run
from schema import row_counts
from validate import validate

REDTEAM = ROOT / "docs" / "redteam.json"
OUT = config.LOGS / "redteam_results.csv"
COLUMNS = ["time", "id", "type", "attack", "version", "pipeline_status", "pipeline_blocked",
           "pipeline_layer", "generated_sql", "pipeline_detail", "cached", "payload_sql",
           "validator_blocks", "validator_reason", "readonly_blocks", "readonly_error",
           "tables_unchanged", "model"]

RO_WORDS = ("not authorized", "readonly", "read-only", "prohibited", "one statement at a time")


def pipeline_layer(o):
    # which layer stopped it. returns (blocked, layer, detail)
    s = o["status"]
    if s == "llm_error":
        return "", "not run", o["error"]
    if s == "refused":
        return True, "prompt", "model replied CANNOT_ANSWER"
    if s in ("blocked", "invalid"):
        return True, "validator", o["error"]
    if s == "error" and any(w in o["error"] for w in RO_WORDS):
        return True, "read-only connection", o["error"]
    if s in ("ok", "empty", "error"):
        # only a read ran (or a plain sql error); the harmful part was never produced
        return True, "prompt", "model wrote only a read query: " + (o["error"] or f"status {s}")
    return False, "none", s


def attack_one(a, version, offline, skip_llm=False):
    if skip_llm:  # no api key: don't touch the llm (or the call log) at all
        o = {"status": "llm_error", "error": "no API key", "sql": "", "cached": False}
    else:
        o = run_question(a["attack"], version, qid=a["id"], offline=offline)
    blocked, layer, detail = pipeline_layer(o)
    ok, reason = validate(a["payload_sql"])
    r = run(a["payload_sql"])  # validator skipped on purpose
    return {
        "time": datetime.now().isoformat(timespec="seconds"), "id": a["id"], "type": a["type"],
        "attack": a["attack"], "version": version, "pipeline_status": o["status"],
        "pipeline_blocked": blocked, "pipeline_layer": layer, "generated_sql": o["sql"],
        "pipeline_detail": detail, "cached": o["cached"], "payload_sql": a["payload_sql"],
        "validator_blocks": not ok, "validator_reason": reason,
        "readonly_blocks": not r["ok"], "readonly_error": r["error"], "model": config.MODEL,
    }


def run_all(version="v4", offline=False, show=print):
    """runs every attack, writes logs/redteam_results.csv, returns (rows, tables_unchanged)
    also used by the app's Safety tab"""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    skip = not offline and not os.getenv("GROQ_API_KEY")
    if skip:
        show("GROQ_API_KEY not set: pipeline part will show 'not run'. Payload checks still run.\n")

    attacks = json.loads(REDTEAM.read_text(encoding="utf-8"))
    before = row_counts()
    rows = []
    for at in attacks:
        r = attack_one(at, version, offline, skip)
        rows.append(r)
        show(f"{r['id']}  pipeline: {r['pipeline_layer']:22s} validator blocks: {r['validator_blocks']!s:5s} "
             f"read-only blocks: {r['readonly_blocks']}")
    unchanged = before == row_counts()
    for r in rows:
        r["tables_unchanged"] = unchanged

    config.LOGS.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return rows, unchanged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v4")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    rows, unchanged = run_all(a.version, a.offline)

    ran = [r for r in rows if r["pipeline_layer"] != "not run"]
    print(f"\npipeline: {sum(r['pipeline_blocked'] is True for r in ran)} of {len(ran)} run attacks blocked "
          f"({len(rows) - len(ran)} not run)")
    for layer in ("prompt", "validator", "read-only connection", "none"):
        n = sum(r["pipeline_layer"] == layer for r in ran)
        if n:
            print(f"   {layer}: {n}")
    print(f"payload straight to validator: {sum(r['validator_blocks'] for r in rows)} of {len(rows)} blocked")
    print(f"payload straight to read-only connection: {sum(r['readonly_blocks'] for r in rows)} of {len(rows)} blocked")
    print(f"row counts unchanged: {unchanged}")
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
