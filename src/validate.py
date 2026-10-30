# checks generated sql before it touches the database
# rules: one statement only, must start with SELECT or WITH, no write/admin keywords,
# no comments, every table in FROM/JOIN must be a real table (or a CTE name)
# returns (ok, reason)

import re

from schema import get_tables

BANNED = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "PRAGMA", "ATTACH",
    "DETACH", "VACUUM", "REINDEX", "ANALYZE", "TRUNCATE", "BEGIN", "COMMIT",
    "ROLLBACK", "SAVEPOINT", "RELEASE", "GRANT", "REVOKE", "LOAD_EXTENSION",
]

# words that end a "FROM table alias" part, so they are not read as an alias
STOP = {
    "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING", "JOIN", "LEFT", "RIGHT", "INNER",
    "OUTER", "CROSS", "FULL", "NATURAL", "ON", "USING", "UNION", "EXCEPT",
    "INTERSECT", "WINDOW", "AS", "OFFSET",
}

# quoted names, plain words, numbers, '' (an emptied string) or any single symbol
TOKEN = re.compile(r'"[^"]*"|`[^`]*`|\[[^\]]*\]|[A-Za-z_][\w$]*|\d+(?:\.\d+)?|\'\'|\S')

# "WITH name AS (" or ", name AS (" (optionally with a column list)
CTE = re.compile(r'(?:\bWITH\b(?:\s+RECURSIVE\b)?|,)\s*("?[A-Za-z_]\w*"?)\s*'
                 r'(?:\([^()]*\)\s*)?AS\s*(?:(?:NOT\s+)?MATERIALIZED\s*)?\(', re.I)

_tables = None


def real_tables():
    global _tables
    if _tables is None:
        _tables = {t.lower() for t in get_tables()}
    return _tables


def blank_strings(sql):
    # replace every '...' string with '' so words/semicolons inside strings are ignored
    # returns None if a quote is never closed
    out, i, n = [], 0, len(sql)
    while i < n:
        if sql[i] == "'":
            j = i + 1
            while True:
                j = sql.find("'", j)
                if j == -1:
                    return None
                if j + 1 < n and sql[j + 1] == "'":  # '' is an escaped quote
                    j += 2
                    continue
                break
            out.append("''")
            i = j + 1
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def clean(name):
    return name.strip('"`[]').lower()


def is_word(tok):
    return bool(re.match(r'^[A-Za-z_"`\[]', tok))


def find_tables(toks):
    # table names that come right after FROM / JOIN (and after commas in FROM a, b)
    up = [t.upper() for t in toks]
    found = []
    for i, t in enumerate(up):
        if t not in ("FROM", "JOIN"):
            continue
        # "a IS DISTINCT FROM b" is a comparison, not a table
        if t == "FROM" and i > 0 and up[i - 1] == "DISTINCT" and i > 1 and up[i - 2] in ("IS", "NOT"):
            continue
        j = i + 1
        while j < len(toks):
            if toks[j] == "(":  # subquery, its own FROM gets checked when we reach it
                break
            name = clean(toks[j])
            if j + 2 < len(toks) and toks[j + 1] == ".":  # schema.table
                name = name + "." + clean(toks[j + 2])
                j += 2
            found.append(name)
            j += 1
            if j < len(toks) and up[j] == "AS":
                j += 1
            if j < len(toks) and is_word(toks[j]) and up[j] not in STOP:
                j += 1  # alias
            if j < len(toks) and toks[j] == ",":
                j += 1
                continue
            break
    return found


def validate(sql):
    if not sql or not sql.strip():
        return False, "empty query"
    if "\x00" in sql:
        return False, "null byte in query"
    if len(sql) > 10000:
        return False, "query too long"

    text = blank_strings(sql)
    if text is None:
        return False, "unclosed string quote"
    if "--" in text or "/*" in text or "*/" in text:
        return False, "comments are not allowed"

    text = text.strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    if ";" in text:
        return False, "more than one statement (stacked queries)"

    toks = TOKEN.findall(text)
    if toks[0].upper() not in ("SELECT", "WITH"):
        return False, f"must start with SELECT or WITH, found '{toks[0][:30]}'"

    words = {t.upper() for t in toks}
    bad = [w for w in BANNED if w in words]
    if bad:
        return False, "forbidden keyword: " + ", ".join(bad)
    if re.search(r"\bREPLACE\s+INTO\b", text, re.I):
        return False, "forbidden keyword: REPLACE INTO"
    if "SELECT" not in words:
        return False, "no SELECT found"

    ctes = {clean(c) for c in CTE.findall(text)}
    for name in find_tables(toks):
        if "." in name:
            schema, name = name.split(".", 1)
            if schema != "main":
                return False, f"schema '{schema}' is not allowed"
        if name.startswith("sqlite_") or name.startswith("pragma_"):
            return False, f"system table '{name}' is not allowed"
        if name not in real_tables() and name not in ctes:
            return False, f"unknown table '{name}'"

    return True, "ok"
