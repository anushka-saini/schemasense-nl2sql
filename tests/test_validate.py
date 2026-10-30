# tests for the validator and the read-only runner
# run: python tests/test_validate.py   (output is also saved to logs/test_validate.txt)

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config
from run_sql import run
from validate import validate

# (sql, should_pass, what it tests)
CASES = [
    # --- normal queries that must be allowed ---
    ("SELECT COUNT(*) FROM orders", True, "simple select"),
    ("SELECT COUNT(*) FROM orders;", True, "one trailing semicolon is fine"),
    ("  \n select order_status, count(*) from orders group by order_status", True, "lowercase + leading whitespace"),
    ("WITH t AS (SELECT order_id, SUM(price) AS s FROM order_items GROUP BY order_id) "
     "SELECT AVG(s) FROM t", True, "CTE name is accepted as a table"),
    ("SELECT c.customer_state, COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
     "GROUP BY c.customer_state", True, "join with aliases"),
    ("SELECT COUNT(*) FROM orders o, customers c WHERE o.customer_id = c.customer_id", True, "comma join"),
    ("SELECT COUNT(*) FROM (SELECT order_id FROM order_items GROUP BY order_id) x", True, "subquery in FROM"),
    ("SELECT COUNT(*) FROM order_reviews WHERE review_comment_message LIKE '%drop table; --%'", True,
     "bad words / ; / -- inside a string literal are ignored"),
    ("SELECT REPLACE(customer_city, ' ', '_') FROM customers LIMIT 5", True, "replace() function is not REPLACE INTO"),
    ("SELECT COUNT(*) FROM main.orders", True, "main.table is fine"),
    ("SELECT COUNT(*) FROM orders WHERE order_approved_at IS DISTINCT FROM order_purchase_timestamp", True,
     "IS DISTINCT FROM is not a table"),
    ("SELECT customer_city FROM customers WHERE customer_city = 'sao paulo''s' LIMIT 1", True, "escaped quote"),

    # --- things that must be blocked ---
    ("", False, "empty"),
    ("INSERT INTO orders VALUES ('x')", False, "insert"),
    ("UPDATE orders SET order_status = 'x'", False, "update"),
    ("DELETE FROM orders", False, "delete"),
    ("DROP TABLE orders", False, "drop"),
    ("ALTER TABLE orders ADD COLUMN x TEXT", False, "alter"),
    ("PRAGMA table_info(orders)", False, "pragma"),
    ("ATTACH DATABASE 'evil.db' AS evil", False, "attach"),
    ("VACUUM INTO 'copy.db'", False, "vacuum into a new file"),
    ("SELECT 1 FROM orders; DROP TABLE orders", False, "stacked statements"),
    ("SeLeCt 1 FrOm orders;\n dRoP tAbLe orders;", False, "stacked, mixed case"),
    ("SELECT * FROM orders; -- harmless", False, "stacked with comment"),
    ("SELECT * FROM orders -- WHERE order_status = 'delivered'", False, "line comment hides a filter"),
    ("SELECT * FROM orders /* DROP */", False, "block comment"),
    ("SELECT * FROM orders WHERE 1=1 /*'*/; DROP TABLE orders; --'", False, "quote trick around comment"),
    ("WITH x AS (SELECT 1) DELETE FROM orders", False, "CTE in front of a delete"),
    ("WITH x AS (SELECT 1) INSERT INTO orders SELECT * FROM orders", False, "CTE in front of an insert"),
    ("SELECT * FROM orders WHERE order_status = 'delivered", False, "unclosed quote"),
    ("SELECT * FROM users", False, "table that does not exist"),
    ("SELECT * FROM orders o JOIN payments p ON o.order_id = p.order_id", False, "hallucinated table in a join"),
    ("SELECT name, sql FROM sqlite_master", False, "system table"),
    ("SELECT * FROM pragma_table_info('orders')", False, "pragma as a table function"),
    ("SELECT * FROM other.orders", False, "attached schema"),
    ("SELECT load_extension('evil.dll')", False, "load_extension"),
    ("REPLACE INTO orders (order_id) VALUES ('x')", False, "replace into"),
    ("CANNOT_ANSWER", False, "refusal text is not sql"),
    ("Here is the query: SELECT COUNT(*) FROM orders", False, "prose before the sql"),
]


def main():
    lines = [f"validator tests run at {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    fails = 0

    lines.append("== 1. validator ==")
    for sql, want, what in CASES:
        ok, reason = validate(sql)
        good = ok == want
        # allowed queries should also really run on the db
        if good and want:
            r = run(sql)
            if not r["ok"]:
                good, reason = False, "validator allowed it but it failed to run: " + r["error"]
        fails += not good
        exp = "allow" if want else "block"
        lines.append(f"{'PASS' if good else 'FAIL'}  expect {exp:5s}  {what:50s}  -> {reason}")

    # these skip the validator on purpose: the connection itself must refuse them
    lines += ["", "== 2. read-only connection (validator skipped) =="]
    before = run("SELECT COUNT(*) AS n FROM orders")["df"].iloc[0, 0]
    attacks = [
        ("DELETE FROM orders", "delete"),
        ("DROP TABLE orders", "drop"),
        ("UPDATE orders SET order_status = 'hacked'", "update"),
        ("CREATE TABLE evil (x)", "create"),
        (f"ATTACH DATABASE '{(config.ROOT / 'logs' / 'evil.db').as_posix()}' AS evil", "attach a new file"),
        ("PRAGMA query_only = OFF", "turn off query_only"),
        ("SELECT * FROM sqlite_master", "read sqlite_master"),
        ("SELECT 1; DROP TABLE orders", "stacked"),
    ]
    for sql, what in attacks:
        r = run(sql)
        good = not r["ok"]
        fails += not good
        lines.append(f"{'PASS' if good else 'FAIL'}  expect block  {what:50s}  -> {r['error'][:80]}")
    # the authorizer catches the above first, so also check mode=ro on its own
    con = sqlite3.connect(config.DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        con.execute("DELETE FROM orders")
        good, msg = False, "delete went through!"
    except sqlite3.Error as e:
        good, msg = "readonly" in str(e), f"{type(e).__name__}: {e}"
    con.close()
    fails += not good
    lines.append(f"{'PASS' if good else 'FAIL'}  expect block  {'delete on plain mode=ro (no authorizer)':50s}  -> {msg}")

    after = run("SELECT COUNT(*) AS n FROM orders")["df"].iloc[0, 0]
    evil = (config.ROOT / "logs" / "evil.db").exists()
    good = before == after and not evil
    fails += not good
    lines.append(f"{'PASS' if good else 'FAIL'}  orders count unchanged ({before} -> {after}), evil.db created: {evil}")

    lines += ["", "== 3. limits =="]
    r = run("SELECT order_id FROM orders")
    good = r["ok"] and len(r["df"]) == config.ROW_LIMIT and r["truncated"]
    fails += not good
    lines.append(f"{'PASS' if good else 'FAIL'}  row limit: got {len(r['df']) if r['ok'] else '-'} rows, truncated={r['truncated']}")

    loop = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    r = run(loop, timeout=2)
    good = not r["ok"] and "timed out" in r["error"]
    fails += not good
    lines.append(f"{'PASS' if good else 'FAIL'}  endless query stopped: {r['error']} after {r['seconds']}s")

    lines += ["", f"total checks failed: {fails}"]
    out = "\n".join(lines)
    print(out)
    (config.LOGS / "test_validate.txt").write_text(out, encoding="utf-8")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
