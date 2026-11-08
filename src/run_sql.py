# runs a query on olist.db with a read-only connection, a time limit and a row limit
# the connection itself refuses writes, even if the validator missed something

import sqlite3
import time
from pathlib import Path

import pandas as pd

import config

# only reading is allowed. anything else (insert, attach, pragma, ...) gets denied
ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
           getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


def authorizer(action, arg1, arg2, dbname, src):
    if action not in ALLOWED:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and arg1 and arg1.lower().startswith("sqlite_"):
        return sqlite3.SQLITE_DENY  # no peeking at sqlite_master etc.
    return sqlite3.SQLITE_OK


def connect(db=config.DB):
    uri = Path(db).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.execute("PRAGMA query_only = ON")  # second lock on top of mode=ro
    con.set_authorizer(authorizer)
    return con


def run(sql, limit=config.ROW_LIMIT, timeout=config.SQL_TIMEOUT, db=config.DB):
    """returns dict: ok, df, error, truncated, seconds.
    limit=None means fetch every row (used for gold queries in scoring)
    retries a couple of times if the file is briefly locked (onedrive / antivirus)"""
    for attempt in range(3):
        r = run_once(sql, limit, timeout, db)
        if r["ok"] or not any(w in r["error"] for w in ("locked", "unable to open")):
            return r
        time.sleep(0.5)
    return r


def run_once(sql, limit, timeout, db):
    t0 = time.time()
    con = None
    try:
        con = connect(db)
        deadline = t0 + timeout
        # sqlite calls this every 10k steps, returning 1 stops the query
        con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall() if limit is None else cur.fetchmany(limit + 1)
        cut = limit is not None and len(rows) > limit
        rows = rows[:limit] if cut else rows
        df = pd.DataFrame(rows, columns=cols)
        return {"ok": True, "df": df, "error": "", "truncated": cut,
                "seconds": round(time.time() - t0, 3)}
    except sqlite3.Error as e:
        msg = str(e)
        if "interrupted" in msg:
            msg = f"query timed out after {timeout}s"
        return {"ok": False, "df": None, "error": f"{type(e).__name__}: {msg}",
                "truncated": False, "seconds": round(time.time() - t0, 3)}
    except Exception as e:  # e.g. sqlite3.Warning for multiple statements
        return {"ok": False, "df": None, "error": f"{type(e).__name__}: {e}",
                "truncated": False, "seconds": round(time.time() - t0, 3)}
    finally:
        if con is not None:
            con.close()
