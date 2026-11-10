# loads everything the app shows. only real files from logs/ and eval/ are used;
# when a file is missing these return None / an empty frame and the app shows an empty state

import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from schema import create_sql, get_columns, row_counts

ROOT = config.ROOT
TEST_SET = ROOT / "eval" / "test_set.json"
REDTEAM_CSV = config.LOGS / "redteam_results.csv"  # first run (before the api key), kept as it was
REDTEAM_DIR = config.LOGS / "redteam"               # every later run gets its own file here
TIER_ORDER = ["simple", "join", "aggregation", "fanout", "ambiguous", "refuse"]
TIER_NAMES = {"simple": "Simple", "join": "Joins", "aggregation": "Aggregations", "fanout": "Fan-out trap",
              "ambiguous": "Ambiguous", "refuse": "Unanswerable / unsafe"}


def api_key_set():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return bool(os.getenv("GROQ_API_KEY"))


def cache_count():
    return len(list(config.CACHE.glob("*.json"))) if config.CACHE.exists() else 0


@st.cache_data(ttl=30)
def test_set():
    return json.loads(TEST_SET.read_text(encoding="utf-8")) if TEST_SET.exists() else []


def samples():
    # dev split only, grouped the way the Ask tab shows them
    groups = {"Simple": [], "Joins": [], "Fan-out trap": [], "Ambiguous": [], "Unanswerable": [], "Unsafe": []}
    name = {"simple": "Simple", "join": "Joins", "fanout": "Fan-out trap", "ambiguous": "Ambiguous"}
    for it in test_set():
        if it["split"] != "dev":
            continue
        if it["tier"] == "refuse":
            groups["Unsafe" if it.get("unsafe") else "Unanswerable"].append(it)
        elif it["tier"] in name:
            groups[name[it["tier"]]].append(it)
    return groups


def find_item(question):
    # the test set item with this exact question (ignoring case and spaces), or None
    key = re.sub(r"\s+", " ", question.strip().lower())
    for it in test_set():
        if re.sub(r"\s+", " ", it["question"].strip().lower()) == key:
            return it
    return None


@st.cache_data(ttl=10)
def results():
    files = sorted(config.LOGS.glob("results_*.csv"))
    if not files:
        return pd.DataFrame()
    parts = []
    for f in files:
        try:
            d = pd.read_csv(f)
            d["file"] = f.name
            parts.append(d)
        except Exception:
            continue  # a broken file is skipped, not shown as numbers
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


@st.cache_data(ttl=10)
def redteam():
    # newest red-team run with the model, else the newest payload-only run, else the first run (old file)
    files = sorted(REDTEAM_DIR.glob("redteam_*.csv"), key=lambda f: f.stat().st_mtime) if REDTEAM_DIR.exists() else []
    full = [f for f in files if "payload-only" not in f.name]
    pick = (full or files)[-1] if files else (REDTEAM_CSV if REDTEAM_CSV.exists() else None)
    if pick is None:
        return None
    d = pd.read_csv(pick)
    d["file"] = pick.name
    return d


@st.cache_data
def schema_info():
    cols = get_columns()
    counts = row_counts()
    return cols, counts, create_sql()


def read_text(path):
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""
