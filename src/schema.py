# read the real schema out of data/olist.db (never type it by hand)

import sqlite3
from pathlib import Path

import config


def connect(db):
    return sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)


def get_tables(db=config.DB):
    con = connect(db)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


def get_columns(db=config.DB):
    # {table: [(column, type), ...]}
    con = connect(db)
    out = {}
    for t in get_tables(db):
        cols = con.execute(f"PRAGMA table_info({t})").fetchall()
        out[t] = [(c[1], c[2]) for c in cols]
    con.close()
    return out


def row_counts(db=config.DB):
    con = connect(db)
    out = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in get_tables(db)}
    con.close()
    return out


def create_sql(db=config.DB):
    # the CREATE TABLE text exactly as sqlite stored it
    con = connect(db)
    rows = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    con.close()
    return "\n\n".join(r[0].strip() + ";" for r in rows)


def names_only(db=config.DB):
    # short form for v1: table(col1, col2, ...)
    lines = []
    for t, cols in get_columns(db).items():
        lines.append(f"{t}({', '.join(c for c, _ in cols)})")
    return "\n".join(lines)


if __name__ == "__main__":
    print(create_sql())
