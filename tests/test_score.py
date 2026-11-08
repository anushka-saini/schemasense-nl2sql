# tests for eval/score.py using hand-made pipeline outputs (no llm)
# run: python tests/test_score.py   (output also saved to logs/test_score.txt)

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import config
from score import same_result, score_item, summarize

lines = [f"score tests run at {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
fails = 0


def check(good, what, detail=""):
    global fails
    fails += not good
    lines.append(f"{'PASS' if good else 'FAIL'}  {what:62s} {detail}")


# ---------- 1. comparing results ----------
lines.append("== 1. same_result() ==")
g = pd.DataFrame({"state": ["SP", "RJ"], "n": [10, 5]})
check(same_result(pd.DataFrame({"x": ["RJ", "SP"], "y": [5, 10]}), g), "row order and column names ignored")
check(same_result(pd.DataFrame({"n": [10, 5], "state": ["SP", "RJ"]}), g), "column order ignored")
check(same_result(pd.DataFrame({"a": [1.004]}), pd.DataFrame({"b": [1.0]})), "floats rounded to 2 places")
check(same_result(pd.DataFrame({"a": [5]}), pd.DataFrame({"b": [5.0]})), "5 equals 5.0")
check(not same_result(pd.DataFrame({"a": [1.01]}), pd.DataFrame({"b": [1.0]})), "1.01 is not 1.00")
extra = pd.DataFrame({"state": ["SP", "RJ"], "n": [10, 5], "pct": [66.7, 33.3]})
check(same_result(extra, g) and not same_result(extra, g, strict=True), "extra column: pass, but not strict pass")
check(not same_result(pd.DataFrame({"state": ["SP"], "n": [10]}), g), "missing row fails")
check(not same_result(pd.DataFrame({"state": ["SP", "RJ"]}), g), "missing column fails")
check(same_result(pd.DataFrame({"a": ["x", "x", "y"]}), pd.DataFrame({"a": ["y", "x", "x"]})), "duplicates counted as a multiset")
check(not same_result(pd.DataFrame({"a": ["x", "y", "y"]}), pd.DataFrame({"a": ["y", "x", "x"]})), "different duplicates fail")
check(same_result(pd.DataFrame({"a": [None]}), pd.DataFrame({"a": [None]})), "NULL equals NULL")

# ---------- 2. scoring items from the real test set ----------
lines += ["", "== 2. score_item() on test_set.json items =="]
items = {it["id"]: it for it in json.loads((ROOT / "eval" / "test_set.json").read_text(encoding="utf-8"))}


def out(sql="", status="ok"):
    return {"sql": sql, "status": status}


def sc(i, sql="", status="ok"):
    return score_item(items[i], out(sql, status))


r = sc("S03", "SELECT COUNT(*) AS canceled_orders FROM orders WHERE order_status = 'canceled'")
check(r["pass"] and r["matched"] == "gold", "S03 correct sql passes", str(r))
r = sc("S03", "SELECT COUNT(*) FROM orders WHERE order_status = 'cancelled'", "ok")
check(not r["pass"], "S03 'cancelled' (two Ls) fails", str(r))

fan = """SELECT ROUND(SUM(i.freight_value), 2), ROUND(SUM(p.payment_value), 2)
FROM orders o JOIN order_items i ON o.order_id = i.order_id JOIN order_payments p ON o.order_id = p.order_id
WHERE o.order_status = 'delivered' AND strftime('%Y', o.order_purchase_timestamp) = '2018'"""
r = sc("F01", fan)
check(not r["pass"], "F01 naive fan-out join fails (trap works)", str(r))
r = sc("F01", items["F01"]["gold_sql"])
check(r["pass"], "F01 gold sql passes against itself", str(r))

r = sc("M02", "SELECT COUNT(DISTINCT customer_id) FROM customers")
check(not r["pass"], "M02 COUNT(customer_id) fails (rule says unique people)", str(r))
r = sc("M08", "SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
              "WHERE o.order_status = 'delivered' AND c.customer_city = 'sao paulo'")
check(r["pass"] and r["matched"] == "alt1", "M08 city interpretation matches alt1", str(r))
r = sc("J05", "SELECT t.product_category_name_english, COUNT(*) AS n FROM order_items i JOIN orders o ON i.order_id = o.order_id "
              "JOIN products p ON i.product_id = p.product_id JOIN product_category_name_translation t "
              "ON p.product_category_name = t.product_category_name WHERE o.order_status = 'delivered' "
              "GROUP BY 1 ORDER BY n DESC LIMIT 5")
check(r["pass"] and not r["strict_pass"], "J05 with an extra count column: pass, not strict", str(r))

check(sc("R01", status="refused")["pass"], "R01 unanswerable + refused -> pass")
check(not sc("R01", "SELECT product_id FROM order_items GROUP BY product_id ORDER BY COUNT(*) DESC LIMIT 1")["pass"],
      "R01 answered with a product_id -> fail")
check(sc("R06", "DELETE FROM orders WHERE order_status = 'canceled'", "blocked")["pass"], "R06 unsafe + blocked by validator -> pass")
check(not sc("R06", "SELECT * FROM orders WHERE order_status = 'canceled'")["pass"], "R06 unsafe answered with a SELECT -> fail")
check(not sc("S01", status="refused")["pass"], "S01 answerable + refused -> fail (over-refusal)")
r = sc("R01", "SELECT 1 -- note", "blocked")
check(not r["pass"] and not r["system_refused"], "R01 unanswerable + blocked for comments -> fail, not a refusal")
r = sc("S01", "SELECT COUNT(*) FROM orders -- all", "blocked")
check(not r["pass"] and not r["system_refused"], "S01 answerable + blocked for comments -> fail, not a refusal")
check(sc("M03", status="refused")["pass"], "M03 refuse_ok + refused -> pass")
check(not sc("S01", "SELECT COUNT(*) FROM orderz", "error")["pass"], "S01 sql error -> fail")

# ---------- 3. summary metrics ----------
lines += ["", "== 3. summarize() on a small made-up results table =="]
rows = [
    # tier, expect_refuse, refuse_ok, status, sql, pass, system_refused
    ("simple", False, False, "ok", "SELECT 1", True, False),
    ("simple", False, False, "error", "SELECT x", False, False),
    ("simple", False, False, "refused", "", False, True),       # over-refusal
    ("ambiguous", False, True, "refused", "", True, True),      # refusal accepted
    ("refuse", True, False, "refused", "", True, True),
    ("refuse", True, False, "blocked", "DROP TABLE x", True, True),
    ("refuse", True, False, "ok", "SELECT 1", False, False),    # answered unanswerable
    ("simple", False, False, "llm_error", "", False, False),    # left out
]
df = pd.DataFrame(rows, columns=["tier", "expect_refuse", "refuse_ok", "status", "generated_sql", "pass", "system_refused"])
df["strict_pass"] = df["pass"]
m, tiers = summarize(df)
want = {"scored": 7, "llm_errors": 1, "execution_accuracy": 57.1, "valid_sql_rate": 50.0,
        "refusal_precision": 75.0, "refusal_recall": 66.7, "over_refusal_rate": 33.3}
for k, v in want.items():
    check(m[k] == v, f"{k} = {v}", f"got {m[k]}")
check(dict(zip(tiers["tier"], tiers["accuracy"])) == {"simple": 33.3, "ambiguous": 100.0, "refuse": 66.7},
      "per-tier accuracy", str(dict(zip(tiers["tier"], tiers["accuracy"]))))

lines += ["", f"total checks failed: {fails}"]
text = "\n".join(lines)
print(text)
(config.LOGS / "test_score.txt").write_text(text, encoding="utf-8")
sys.exit(1 if fails else 0)
