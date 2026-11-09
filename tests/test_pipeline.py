# tests for phase 3: reply parsing, prompt building, the v3 examples and the repair loop
# the llm is replaced by a fake that returns scripted replies, so no api key is needed
# and nothing is written to logs/cache or logs/calls.csv
# run: python tests/test_pipeline.py   (output also saved to logs/test_pipeline.txt)

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config
import pipeline
from build_prompt import build, load

lines = [f"pipeline tests run at {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
fails = 0


def check(good, what, detail=""):
    global fails
    fails += not good
    lines.append(f"{'PASS' if good else 'FAIL'}  {what:58s} {detail}")


# ---------- 1. parsing model replies ----------
lines.append("== 1. parse() ==")
PARSE = [
    ("```sql\nSELECT COUNT(*) FROM orders;\n```\nExplanation: Counts all orders.",
     "SELECT COUNT(*) FROM orders;", "Counts all orders.", False, "fenced sql + explanation"),
    ("Here is the query:\n```sqlite\nSELECT 1\n```\n**Explanation:** One.",
     "SELECT 1", "One.", False, "prose before fence, bold label"),
    ("SELECT COUNT(*)\nFROM orders\n\nThis counts orders.",
     "SELECT COUNT(*)\nFROM orders", "This counts orders.", False, "no fence, blank line then text"),
    ("WITH t AS (SELECT 1)\nSELECT * FROM t\nExplanation: cte.",
     "WITH t AS (SELECT 1)\nSELECT * FROM t", "cte.", False, "no fence, explanation right after"),
    ("CANNOT_ANSWER", "", "", True, "plain refusal"),
    ("**CANNOT_ANSWER**\nThere is no email column.", "", "There is no email column.", True, "bold refusal + reason"),
    ("```\nCANNOT_ANSWER\n```", "", "", True, "refusal inside a fence"),
    ("Sorry, the schema has no email field. CANNOT_ANSWER", "", "Sorry, the schema has no email field.", True, "refusal at the end"),
    ("I think you should look at the orders table.", "", "", False, "no sql, no refusal"),
    ("", "", "", False, "empty reply"),
]
for text, sql, expl, ref, what in PARSE:
    got = pipeline.parse(text)
    check(got == (sql, expl, ref), what, "" if got == (sql, expl, ref) else f"got {got!r}")

# ---------- 2. prompt building ----------
lines += ["", "== 2. build_prompt ==", ]
q = "How many orders are there?"
p = {v: build(v, q) for v in ["v0", "v1", "v2", "v3", "v4"]}
for v, text in p.items():
    left = re.findall(r"\{(schema|question)\}", text)
    check(q in text and not left, f"{v} has the question and no empty slots", f"{len(text):,} chars")
check("CREATE TABLE" not in p["v0"] and "order_items" not in p["v0"], "v0 has no schema")
check("order_items(order_id" in p["v1"] and "CREATE TABLE" not in p["v1"], "v1 has table/column names, not CREATE TABLE")
check("CREATE TABLE" in p["v2"] and "Business rules" in p["v2"] and "Examples" not in p["v2"], "v2 has CREATE TABLE + business rules, no examples")
check(p["v3"].startswith(p["v2"].split("Question: " + q)[0]) and "Examples" in p["v3"], "v3 = v2 text + examples")
check(p["v3"] == p["v4"], "v4 prompt is identical to v3 (only the repair loop differs)")
inj = build("v2", "What is {schema} and {question}?")
check("What is {schema} and {question}?" in inj, "slots inside the question are not filled again")

# ---------- 3. v3 worked examples really run ----------
lines += ["", "== 3. v3 example SQL runs on olist.db =="]
blocks = re.findall(r"```sql\n(.*?)```", load("v3"), re.S)
check(len(blocks) == 4, "v3 has 4 SQL examples (+1 CANNOT_ANSWER example)", f"found {len(blocks)}")
for b in blocks:
    status, problem, res = pipeline.check_and_run(b)
    shown = res["df"].to_dict("records") if res and res["ok"] else problem
    check(status == "ok", "example: " + b.strip().splitlines()[0][:50], str(shown))

# ---------- 4. pipeline with a fake llm ----------
lines += ["", "== 4. run_question() with scripted replies =="]
calls = []


def fake(replies):
    # returns a fake ask() that hands out the replies in order
    def ask(prompt, tag, repeat=1, offline=False):
        calls.append((tag, prompt))
        r = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return {"text": r, "cached": False, "latency_s": 0.5, "total_tokens": 100, "finish_reason": "stop"}
    return ask


GOOD = "```sql\nSELECT COUNT(*) AS n FROM orders WHERE order_status = 'delivered'\n```\nExplanation: Counts delivered orders."
BAD_COL = "```sql\nSELECT COUNT(*) FROM orders WHERE status = 'delivered'\n```\nExplanation: x"
BAD_TABLE = "```sql\nSELECT COUNT(*) FROM payments\n```"
EMPTY = "```sql\nSELECT COUNT(*) AS n FROM orders WHERE order_status = 'Delivered' GROUP BY order_status\n```"
DROP = "```sql\nDROP TABLE orders\n```"
STACK = "```sql\nSELECT 1 FROM orders; DELETE FROM orders\n```"

SCEN = [
    # (version, replies, want status, want retries, what)
    ("v4", [BAD_COL, GOOD], "ok", 1, "v4: sql error -> repaired on retry 1"),
    ("v4", [BAD_TABLE, GOOD], "ok", 1, "v4: unknown table -> repaired"),
    ("v4", [EMPTY, GOOD], "ok", 1, "v4: empty result -> repaired"),
    ("v4", ["no sql here", GOOD], "ok", 1, "v4: no sql in reply -> repaired"),
    ("v4", [BAD_COL, BAD_COL, BAD_COL], "error", 2, "v4: still broken after 2 repairs -> stops"),
    ("v4", [DROP, GOOD], "blocked", 0, "v4: DROP is blocked, never repaired"),
    ("v4", [STACK, GOOD], "blocked", 0, "v4: stacked delete is blocked, never repaired"),
    ("v4", ["CANNOT_ANSWER", GOOD], "refused", 0, "v4: refusal is final"),
    ("v3", [BAD_COL, GOOD], "error", 0, "v3: no repair loop"),
    ("v1", [GOOD], "ok", 0, "v1: good sql runs"),
    ("v4", [RuntimeError("Offline mode: this prompt is not in the cache.")], "llm_error", 0, "llm failure is reported, no crash"),
]
for v, replies, want, retries, what in SCEN:
    calls.clear()
    pipeline.ask = fake(replies)
    o = pipeline.run_question("How many orders were delivered?", v, qid="T1")
    good = o["status"] == want and o["retries"] == retries
    check(good, what, f"status={o['status']} retries={o['retries']} calls={len(calls)} tags={[c[0] for c in calls]}")

# the repair prompt must carry the original prompt, the bad sql and the error
calls.clear()
pipeline.ask = fake([BAD_COL, GOOD])
o = pipeline.run_question("How many orders were delivered?", "v4", qid="T1")
rp = calls[1][1]
check(rp.startswith(o["prompt"]) and "WHERE status = 'delivered'" in rp and "no such column: status" in rp,
      "repair prompt = original prompt + bad sql + sqlite error")
check(o["rows"].iloc[0, 0] == 96478 and o["explanation"] == "Counts delivered orders.",
      "final rows and explanation come from the repaired answer", f"n={o['rows'].iloc[0, 0]}")
check([s["name"] for s in o["steps"]] == ["Prompt built", "SQL generated", "Validated", "Executed", "Repair", "Explained"],
      "steps list includes Repair when used")

# ---------- 5. lenient extractor (v0 baseline analysis only) ----------
lines += ["", "== 5. lenient_parse() =="]
lp = pipeline.lenient_parse
FENCED = "Here:\n```sql\n-- count orders\nSELECT COUNT(*) -- all\nFROM orders; -- done\n```\nExplanation: counts"
check(lp(FENCED)[0] == "SELECT COUNT(*) \nFROM orders", "comments and trailing ; removed inside a fence", repr(lp(FENCED)[0]))
check(pipeline.parse(FENCED)[0].startswith("-- count"), "strict parse() is unchanged (keeps the comment)")
WRITE_THEN_READ = "```sql\n-- change\nUPDATE orders SET order_status = 'x';\n```\ncheck:\n```sql\nSELECT * FROM orders;\n```"
check(lp(WRITE_THEN_READ)[0].startswith("UPDATE"), "a write is kept, not swapped for a later SELECT")
check(lp("SELECT 1 WHERE 'a--b' = 'a--b'; SELECT 2")[0] == "SELECT 1 WHERE 'a--b' = 'a--b'",
      "-- and ; inside quotes are kept, second statement dropped")
check(lp("Sure, use\n\nSELECT a\nFROM t\n\nThis returns a.")[0] == "SELECT a\nFROM t", "prose before and after removed")
check(lp("CANNOT_ANSWER\nno email column")[2] is True, "CANNOT_ANSWER still a refusal")
check(lp("no sql here")[0] == "", "no sql -> empty")

lines += ["", f"total checks failed: {fails}"]
out = "\n".join(lines)
print(out)
(config.LOGS / "test_pipeline.txt").write_text(out, encoding="utf-8")
sys.exit(1 if fails else 0)
