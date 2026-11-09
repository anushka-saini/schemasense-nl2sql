# runs one question end to end:
# build prompt -> ask llm -> pull out sql -> validate -> run read-only
# v4 only: if the sql is broken, errors or returns nothing, send a repair prompt (max 2 times)

import re
import sys

from build_prompt import build, build_repair
from llm import ask
from run_sql import run
from validate import is_unsafe, validate

MAX_REPAIRS = 2
REPAIR_VERSIONS = {"v4"}

FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n?(.*?)```", re.S)

# status values:
#   ok       ran and returned rows
#   empty    ran but returned 0 rows
#   refused  model said CANNOT_ANSWER
#   blocked  validator stopped an unsafe query (write, stacked, comment trick, ...)
#   invalid  no sql in the reply, or validator rejected it (e.g. unknown table)
#   error    sqlite error when running it
#   llm_error  api call failed (or offline and not cached)


def clean_expl(text):
    # first real line, without "Explanation:" and markdown stars
    for line in (text or "").splitlines():
        line = line.strip().strip("*").strip()
        line = re.sub(r"^\**explanation\**\s*:\s*\**", "", line, flags=re.I).strip()
        if line:
            return line
    return ""


def parse(text):
    """split a model reply into (sql, explanation, refused)"""
    t = (text or "").strip()
    if not t:
        return "", "", False

    first = t.splitlines()[0].strip().strip("`*\"' .:")
    if first.upper().startswith("CANNOT_ANSWER"):
        rest = t.split("\n", 1)[1] if "\n" in t else ""
        return "", clean_expl(rest), True

    m = FENCE.search(t)
    if m:
        sql = m.group(1).strip()
        expl = t[m.end():]
    else:
        # no ``` block: sql starts at the first SELECT/WITH line and stops at a blank line
        lines = t.splitlines()
        start = next((i for i, l in enumerate(lines) if re.match(r"\s*(SELECT|WITH)\b", l, re.I)), None)
        if start is None:
            if "CANNOT_ANSWER" in t:
                return "", clean_expl(t.replace("CANNOT_ANSWER", "")), True
            return "", "", False
        end = start
        while end < len(lines) and lines[end].strip() and not re.match(r"\s*\**explanation", lines[end], re.I):
            end += 1
        sql = "\n".join(lines[start:end]).strip()
        expl = "\n".join(lines[end:])

    if sql.strip(" ;").upper() == "CANNOT_ANSWER":
        return "", clean_expl(expl), True
    return sql, clean_expl(expl), False


# ---------- lenient extractor (analysis only, never used by the app or the normal eval) ----------
# used to give v0 a fairer baseline: v0 was never told the answer format, so it writes markdown,
# prose and -- comments around its sql. the lenient extractor:
#   1. takes the first ``` block that has sql in it (or the whole reply if there is no block)
#   2. drops -- and /* */ comments (outside quotes)
#   3. takes the FIRST statement: from the first line that starts with a sql keyword up to the first ;
# a write statement is kept as it is (so the validator still blocks it). we do NOT skip ahead to a
# later SELECT, because then "UPDATE ...; -- check with: SELECT ..." would look like a safe answer.
STMT_START = re.compile(r"^[ \t]*(SELECT|WITH|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|PRAGMA|ATTACH|DETACH|REPLACE|VACUUM)\b",
                        re.I | re.M)
SQL_WORD = re.compile(r"\b(SELECT|WITH|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|PRAGMA|ATTACH)\b", re.I)


def strip_comments(sql):
    # walk the text so a -- inside 'a string' is kept
    out, i, q = [], 0, None
    while i < len(sql):
        ch = sql[i]
        if q:
            out.append(ch)
            if ch == q:
                q = None
            i += 1
        elif ch in ("'", '"'):
            q = ch
            out.append(ch)
            i += 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = len(sql) if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = len(sql) if j == -1 else j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def first_statement(sql):
    # cut at the first ; that is not inside quotes
    q = None
    for i, ch in enumerate(sql):
        if q:
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
        elif ch == ";":
            return sql[:i]
    return sql


def lenient_parse(text):
    """like parse(), but forgiving about format. returns (sql, explanation, refused)"""
    sql0, expl, refused = parse(text)
    if refused:
        return sql0, expl, refused
    t = (text or "").strip()
    blocks = [b for b in FENCE.findall(t) if SQL_WORD.search(b)]
    if blocks:
        body = strip_comments(blocks[0])
        m = STMT_START.search(body)
        sql = first_statement(body[m.start():]) if m else ""
    else:
        body = strip_comments(t)
        m = STMT_START.search(body)
        if not m:
            return "", expl, False
        lines = body[m.start():].splitlines()
        end = 0
        while end < len(lines) and lines[end].strip():  # no fence: stop at the first blank line
            end += 1
        sql = first_statement("\n".join(lines[:end]))
    return sql.strip(), expl, False


def check_and_run(sql):
    """validator + read-only run. returns (status, problem, result)
    also used by the app for hand-edited sql"""
    if not sql:
        return "invalid", "no SQL found in the reply", None
    ok, reason = validate(sql)
    if not ok:
        return ("blocked" if is_unsafe(reason) else "invalid"), reason, None
    res = run(sql)
    if not res["ok"]:
        return "error", res["error"], res
    if len(res["df"]) == 0:
        return "empty", "The query ran but returned 0 rows.", res
    return "ok", "", res


def make_steps(out, last):
    # status badge for each step, used by the app (state: ok / warn / fail / skip)
    s = out["status"]
    steps = [{"name": "Prompt built", "state": "ok", "detail": f"{out['version']}, {len(out['prompt']):,} characters"}]

    if s == "llm_error":
        steps.append({"name": "SQL generated", "state": "fail", "detail": out["error"]})
    elif s == "refused":
        steps.append({"name": "SQL generated", "state": "warn", "detail": "Model replied CANNOT_ANSWER"})
    elif not out["sql"]:
        steps.append({"name": "SQL generated", "state": "fail", "detail": "No SQL found in the reply"})
    else:
        steps.append({"name": "SQL generated", "state": "ok", "detail": "cached" if last.get("cached") else "from API"})

    if s in ("blocked", "invalid") and out["sql"]:
        steps.append({"name": "Validated", "state": "fail", "detail": out["error"]})
    elif s in ("ok", "empty", "error"):
        steps.append({"name": "Validated", "state": "ok", "detail": "single read-only SELECT"})
    else:
        steps.append({"name": "Validated", "state": "skip", "detail": ""})

    if s == "ok":
        steps.append({"name": "Executed", "state": "ok", "detail": f"{len(out['rows'])} rows" + (" (row limit hit)" if out["truncated"] else "")})
    elif s == "empty":
        steps.append({"name": "Executed", "state": "warn", "detail": "0 rows"})
    elif s == "error":
        steps.append({"name": "Executed", "state": "fail", "detail": out["error"]})
    else:
        steps.append({"name": "Executed", "state": "skip", "detail": ""})

    if out["retries"]:
        steps.append({"name": "Repair", "state": "ok" if s in ("ok", "refused") else "fail",
                      "detail": f"{out['retries']} repair attempt(s)"})

    if out["explanation"]:
        steps.append({"name": "Explained", "state": "warn" if s == "refused" else "ok", "detail": out["explanation"]})
    else:
        steps.append({"name": "Explained", "state": "skip", "detail": "no explanation in the reply"})
    return steps


def run_question(question, version="v4", qid="", repeat=1, offline=False, lenient=False):
    # lenient=True swaps in lenient_parse (only for the v0 baseline analysis, see above)
    reader = lenient_parse if lenient else parse
    prompt = build(version, question)
    out = {"question": question, "version": version, "qid": qid, "repeat": repeat,
           "prompt": prompt, "sql": "", "rows": None, "status": "", "error": "",
           "explanation": "", "retries": 0, "truncated": False, "attempts": [],
           "latency_s": 0.0, "tokens": 0, "cached": True}

    p, tag = prompt, f"{version}:{qid}"
    last = {}
    for attempt in range(MAX_REPAIRS + 1):
        try:
            r = ask(p, tag, repeat=repeat, offline=offline)
        except Exception as e:
            out["status"], out["error"] = "llm_error", f"{type(e).__name__}: {e}"
            out["cached"] = False
            break
        out["latency_s"] += r["latency_s"]
        out["tokens"] += r["total_tokens"] or 0
        out["cached"] = out["cached"] and r["cached"]

        sql, expl, refused = reader(r["text"])
        if refused:
            status, problem, res = "refused", "", None
        else:
            status, problem, res = check_and_run(sql)

        last = {"attempt": attempt, "prompt": p, "raw": r["text"], "sql": sql, "status": status,
                "problem": problem, "cached": r["cached"], "finish_reason": r["finish_reason"]}
        out["attempts"].append(last)
        out.update(sql=sql, explanation=expl, status=status, error=problem)
        out["rows"] = res["df"] if res and res["ok"] else None
        out["truncated"] = bool(res and res["truncated"])

        # stop unless this is v4 and the problem is fixable
        if version not in REPAIR_VERSIONS or status in ("ok", "refused", "blocked") or attempt == MAX_REPAIRS:
            break
        p = build_repair(prompt, sql or r["text"], problem)
        tag = f"{version}-r{attempt + 1}:{qid}"
        out["retries"] += 1

    out["latency_s"] = round(out["latency_s"], 3)
    out["steps"] = make_steps(out, last)
    return out


if __name__ == "__main__":
    # python src/pipeline.py v3 "How many orders were delivered?" [--offline]
    args = [a for a in sys.argv[1:] if a != "--offline"]
    v = args[0] if args else "v4"
    q = args[1] if len(args) > 1 else "How many orders were delivered?"
    o = run_question(q, v, offline="--offline" in sys.argv)
    for st in o["steps"]:
        print(f"[{st['state']:4s}] {st['name']:14s} {st['detail']}")
    print("\nstatus:", o["status"], "| retries:", o["retries"], "| tokens:", o["tokens"],
          "| latency:", o["latency_s"], "s | cached:", o["cached"])
    print("\nSQL:\n" + (o["sql"] or "-"))
    print("\nexplanation:", o["explanation"] or "-")
    if o["rows"] is not None:
        print("\n" + o["rows"].head(20).to_string(index=False))
