# fills the {schema} / {question} slots in prompts/vN.txt

import re

import config
from schema import create_sql, names_only

VERSIONS = ["v0", "v1", "v2", "v3", "v4"]

# which schema text each version gets (v0 gets none)
SCHEMA_FORM = {"v0": None, "v1": "names", "v2": "create", "v3": "create", "v4": "create"}

SLOT = re.compile(r"\{(schema|question|prompt|sql|error)\}")


def fill(template, values):
    # one pass, so a question that contains "{schema}" is not filled in again
    return SLOT.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def load(name):
    return (config.PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


def schema_text(version):
    form = SCHEMA_FORM[version]
    if form == "names":
        return names_only()
    if form == "create":
        return create_sql()
    return ""


def build(version, question):
    if version not in VERSIONS:
        raise ValueError(f"unknown prompt version {version}")
    return fill(load(version), {"schema": schema_text(version), "question": question.strip()})


def build_repair(prompt, sql, error):
    return fill(load("repair"), {"prompt": prompt, "sql": sql, "error": error})


if __name__ == "__main__":
    import sys
    v = sys.argv[1] if len(sys.argv) > 1 else "v2"
    q = sys.argv[2] if len(sys.argv) > 2 else "How many orders were delivered?"
    print(build(v, q))
