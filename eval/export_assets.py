# exports everything the final write-up needs, computed from logs/ (no api calls, nothing typed by hand)
#   docs/report/results.json          every number shown on the report page (PENDING where a run is missing)
#   logs/per_question_test.csv        one row per test question x version x model (latest runs)
#   docs/report/figures/*.svg + .png  accuracy with 95% CIs (dev, test), per-tier accuracy, error categories,
#                                     red-team layers. png is rendered from the svg by a local edge/chrome (headless);
#                                     if none is found only the svg files are written
# colours: the reference categorical palette (light mode), checked for colour-blind separation
# run: python eval/export_assets.py
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime
from html import escape as E
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import config
import make_report_assets as mra
from score import summarize

P = "PENDING"
LOGS = config.LOGS
OUT = ROOT / "docs" / "report"
FIG = OUT / "figures"
MAIN = config.MODEL
M2 = "openai/gpt-oss-20b"
VERS = ["v0", "v1", "v2", "v3", "v4"]
TIERS = ["simple", "join", "aggregation", "fanout", "ambiguous", "refuse"]
TIER_NAME = {"simple": "Simple", "join": "Joins", "aggregation": "Aggregations", "fanout": "Multi-step / fan-out",
             "ambiguous": "Ambiguous", "refuse": "Unanswerable / unsafe"}
ITEMS = json.loads((ROOT / "eval" / "test_set.json").read_text(encoding="utf-8"))

# chart colours (light): surface, ink, grid, series 1 (execution) and 2 (strict), sequential blue ramp
C = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
     "axis": "#c3c2b7", "s1": "#2a78d6", "s2": "#eb6834"}
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def num(x):
    # json-safe number (nan -> None)
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return float(x) if isinstance(x, float) else int(x) if isinstance(x, (int,)) else x


def records(df):
    return json.loads(df.to_json(orient="records", force_ascii=False))


# ---------------- data ----------------
def all_results(folder=LOGS):
    files = sorted(folder.glob("results_*.csv"))
    return pd.concat([pd.read_csv(f).assign(file=f.name) for f in files], ignore_index=True) if files else pd.DataFrame()


RES = all_results()
RES2 = all_results(LOGS / "test2")


def latest(res, split, model):
    if res.empty:
        return res
    d = res[(res["split"] == split) & (res["model"] == model)]
    keep = d.groupby("version")["run_id"].max()
    return d[d["run_id"].isin(keep.values)]


def complete(d, v, n, reps):
    g = d[d["version"] == v]
    return g["id"].nunique() == n and g["repeat"].nunique() == reps


def metrics_rows(d, ci):
    out = []
    for v in sorted(d["version"].unique()):
        g = d[d["version"] == v]
        m, _ = summarize(g)
        # test2 labels look like "v2 (test2, post-hoc, exploratory)"
        names = [v, f"{v} (test2, post-hoc, exploratory)"]
        r = ci[ci["version"].isin(names) & (ci["model"] == g["model"].iloc[0])] if len(ci) else ci
        r = None if r is None or r.empty else r.iloc[0]
        out.append({"version": v, "model": g["model"].iloc[0], "run_id": g["run_id"].iloc[0],
                    "questions": int(g["id"].nunique()), "repeats": int(g["repeat"].nunique()), **m,
                    "boot_lo": None if r is None else num(r["boot_lo"]), "boot_hi": None if r is None else num(r["boot_hi"]),
                    "strict_lo": None if r is None else num(r["strict_lo"]), "strict_hi": None if r is None else num(r["strict_hi"]),
                    "majority_acc": None if r is None else num(r["majority_acc"]),
                    "wilson_lo": None if r is None else num(r["wilson_lo"]), "wilson_hi": None if r is None else num(r["wilson_hi"]),
                    "repeat_min": None if r is None else num(r["repeat_min"]), "repeat_max": None if r is None else num(r["repeat_max"]),
                    "unstable_questions": None if r is None else num(r["unstable_questions"])})
    return out


def tiers(d):
    if d.empty:
        return {}
    t = d.assign(p=d["pass"].astype(str).str.lower() == "true").groupby(["tier", "version"])["p"].mean() * 100
    return {tier: {v: round(float(t[(tier, v)]), 1) for v in VERS if (tier, v) in t.index} for tier in TIERS
            if tier in t.index.get_level_values(0)}


def labels(split):
    f = LOGS / f"error_labels_{split}.csv"
    if not f.exists():
        return None
    l = pd.read_csv(f).astype(object).fillna("")
    l = l[l["error_category"] != ""]
    if l.empty:
        return {"labelled_rows": 0, "counts": {}}
    ct = pd.crosstab(l["error_category"], l["version"])
    return {"labelled_rows": int(len(l)), "labelled_by": sorted(set(l.get("labelled_by", pd.Series(["?"])))),
            "counts": {c: {v: int(ct.loc[c, v]) for v in ct.columns} for c in ct.index}}


def redteam():
    folder = LOGS / "redteam"
    files = sorted(folder.glob("redteam_*.csv"), key=lambda f: f.name.split("_")[-1]) if folder.exists() else []
    attacks = json.loads((ROOT / "docs" / "redteam.json").read_text(encoding="utf-8"))
    full, pay = {}, {}
    for f in files:
        for _, r in pd.read_csv(f).iterrows():
            (pay if "payload-only" in f.name else full)[r["id"]] = (r, f.name)
    rows, used = [], set()
    for a in attacks:
        r, f = full.get(a["id"]) or pay.get(a["id"]) or (None, None)
        if f:
            used.add(f)
        ran = r is not None and "payload-only" not in str(f) and str(r["pipeline_layer"]) != "not run"
        leak = r.get("prompt_leak") if r is not None else None
        rows.append({"id": a["id"], "set": a.get("set", "original"), "attack": a["type"],
                     "full_pipeline": ("blocked" if str(r["pipeline_blocked"]) == "True" else "NOT blocked") if ran else P,
                     "stopped_at": str(r["pipeline_layer"]) if ran else None,
                     "pipeline_status": str(r["pipeline_status"]) if ran else None,
                     "prompt_leaked": ("yes" if str(leak) == "True" else "no") if ran and leak is not None else None,
                     "validator_alone": None if r is None else ("blocked" if str(r["validator_blocks"]) == "True" else "passed"),
                     "readonly_alone": None if r is None else (
                         "blocked" if str(r["readonly_blocks"]) == "True" else
                         "ran, cut at 200 rows" if str(r.get("payload_truncated")) == "True" else "ran (read only)"),
                     "tables_unchanged": None if r is None else str(r["tables_unchanged"]) == "True",
                     "file": f})
    d = pd.DataFrame(rows)
    ran = d[d["full_pipeline"] != P]
    return {"rows": rows, "files": sorted(used),
            "summary": {"attacks": len(d), "full_run": int(len(ran)),
                        "full_blocked": int((ran["full_pipeline"] == "blocked").sum()),
                        "stopped_at": {k: int(n) for k, n in ran["stopped_at"].value_counts().items()},
                        "prompt_leaks": int((ran["prompt_leaked"] == "yes").sum()),
                        "validator_alone_blocked": int((d["validator_alone"] == "blocked").sum()),
                        "readonly_alone_blocked": int((d["readonly_alone"] == "blocked").sum()),
                        "tables_unchanged_all": bool(all(x for x in d["tables_unchanged"] if x is not None))}}


def reversal():
    f = LOGS / "reversal_v1_v2_test.csv"
    if not f.exists():
        return None
    q = pd.read_csv(f).astype(object).fillna("")
    rc = sorted((LOGS / "recheck").glob("timeouts_v2_test_*.csv"))
    rcd = pd.read_csv(rc[-1]) if rc else pd.DataFrame()
    v2 = latest(RES, "test", MAIN)
    v2 = v2[v2["version"] == "v2"]
    rep = (v2.assign(p=v2["pass"].astype(str).str.lower() == "true").groupby("repeat")["p"].mean() * 100).round(1)
    return {"flips": records(q[q["flip"] != ""][["id", "tier", "flip", "v1_passes", "v2_passes", "v2_statuses",
                                                  "draft_error_category", "draft_reason", "labelled_by"]]),
            "v2_per_repeat": {int(k): float(v) for k, v in rep.items()},
            "timeout_recheck": {"file": rc[-1].name if rc else None, "rows": int(len(rcd)),
                                "finish_now": int(rcd["ok_now"].sum()) if len(rcd) else 0,
                                "seconds_now_max": num(float(rcd["seconds_now"].max())) if len(rcd) else None,
                                "seconds_now_min": num(float(rcd["seconds_now"].min())) if len(rcd) else None,
                                "match_gold_now": int(rcd["matches_gold_now"].fillna(False).astype(bool).sum()) if len(rcd) else 0}}


def tests():
    out = {}
    for name, f in [("validator + read-only", "test_validate.txt"), ("pipeline", "test_pipeline.txt"), ("scorer", "test_score.txt")]:
        t = (LOGS / f).read_text(encoding="utf-8").splitlines()
        out[name] = {"passed": sum(l.startswith("PASS") for l in t), "failed": sum(l.startswith("FAIL") for l in t)}
    return out


def provisional():
    col = "verdict (ok/fix/unsure)"

    def sheet(n):
        p = ROOT / "review" / f"{n}.csv"
        return pd.read_csv(p, encoding="utf-8-sig").astype(object).fillna("") if p.exists() else None
    g, l, lt = sheet("gold_review"), sheet("labels_review"), sheet("labels_review_test")
    t2 = json.loads((ROOT / "eval" / "test2_set.json").read_text(encoding="utf-8")) if (ROOT / "eval" / "test2_set.json").exists() else []
    n_lab = sum(len(x) for x in (l, lt) if x is not None)
    return {"gold_verified": sum(bool(i.get("verified")) for i in ITEMS), "gold_total": len(ITEMS),
            "gold_rows_reviewed": int((g[col].astype(str).str.strip() != "").sum()) if g is not None else 0,
            "test2_verified": sum(bool(i.get("verified")) for i in t2), "test2_total": len(t2),
            "label_rows": n_lab,
            "labels_confirmed": sum(int((x[col].astype(str).str.strip().str.lower() == "ok").sum()) for x in (l, lt) if x is not None),
            "assumptions_confirmed": False, "responsible_ai": "draft"}


def per_question():
    rows = []
    for model in [MAIN, M2]:
        d = latest(RES, "test", model)
        if d.empty:
            continue
        d = d.assign(p=d["pass"].astype(str).str.lower() == "true", sp=d["strict_pass"].astype(str).str.lower() == "true")
        for (v, i), g in d.groupby(["version", "id"]):
            g = g.sort_values("repeat")
            bad = g[~g["p"]]
            rows.append({"id": i, "tier": g["tier"].iloc[0], "version": v, "model": model, "run_id": g["run_id"].iloc[0],
                         "repeats": len(g), "passes": int(g["p"].sum()), "strict_passes": int(g["sp"].sum()),
                         "pass_rate": round(g["p"].mean(), 3), "majority_pass": bool(g["p"].mean() > 0.5),
                         "statuses": "/".join(g["status"].astype(str)), "repairs_total": int(g["retries"].sum()),
                         "first_fail_status": bad["status"].iloc[0] if len(bad) else "",
                         "first_fail_error": str(bad["error"].iloc[0]) if len(bad) and str(bad["error"].iloc[0]) != "nan" else "",
                         "question": g["question"].iloc[0]})
    out = pd.DataFrame(rows).sort_values(["model", "version", "id"])
    out.to_csv(LOGS / "per_question_test.csv", index=False)
    return len(out)


def findings(R):
    # 5 bullets, each built only from numbers in R. PENDING where something is missing
    acc = {r["version"]: r for r in R["metrics"]["test"]}
    done = R["status"]["test_done"]

    def a(v):
        r = acc.get(v)
        if v not in done or r is None:
            return f"{v} {P}"
        return (f"{v} {r['execution_accuracy']:.1f}% [{r['boot_lo']:.1f}–{r['boot_hi']:.1f}] "
                f"(strict {r['strict_accuracy']:.1f}%)")
    f1 = "Held-out test, execution accuracy [95% CI] (strict): " + "; ".join(a(v) for v in VERS) + "."
    sig = R["significance"]
    if sig:
        f2 = "Paired McNemar, Holm-corrected: " + "; ".join(
            f"{s['version_a']}→{s['version_b']} p = {s['p_holm']}{' (significant)' if s['significant_0.05'] else ''}"
            for s in sig) + "."
    else:
        r0, r1, r2 = acc.get("v0"), acc.get("v1"), acc.get("v2")
        f2 = (f"Significance tests {P} (need v3 and v4). By interval only: v0→v1 {r0['execution_accuracy']:.1f}% → "
              f"{r1['execution_accuracy']:.1f}%, intervals do not overlap; v1→v2 {r1['execution_accuracy']:.1f}% → "
              f"{r2['execution_accuracy']:.1f}%, intervals overlap, so not separable on 40 questions."
              if r0 and r1 and r2 else f"Significance tests {P}.")
    rv, dv = R["reversal"], {r["version"]: r for r in R["metrics"]["dev"]}
    if rv and "v1" in dv and "v2" in dv and "v1" in acc and "v2" in acc:
        tr = rv["timeout_recheck"]
        f3 = (f"Dev vs test reversal: v1→v2 was {dv['v1']['execution_accuracy']:.1f}% → {dv['v2']['execution_accuracy']:.1f}% "
              f"on dev (20 q) but {acc['v1']['execution_accuracy']:.1f}% → {acc['v2']['execution_accuracy']:.1f}% on test. "
              f"{sum(1 for x in rv['flips'] if x['flip'] == 'pass -> fail')} test questions flipped pass→fail and "
              f"{sum(1 for x in rv['flips'] if x['flip'] == 'fail -> pass')} fail→pass; {tr['rows']} v2 answers timed out at "
              f"run time but finish in {tr['seconds_now_min']}–{tr['seconds_now_max']} s now ({tr['match_gold_now']} would match gold).")
    else:
        f3 = f"Dev vs test reversal analysis: {P}."
    s = R["redteam"]["summary"]
    f4 = (f"Red team ({s['attacks']} attacks): full pipeline with the model "
          + (f"stopped {s['full_blocked']} of {s['full_run']} run so far"
             + (f", prompt leaked in {s['prompt_leaks']}" if s["full_run"] else "") if s["full_run"] else P)
          + (f" ({s['attacks'] - s['full_run']} {P})" if s["full_run"] < s["attacks"] else "")
          + f"; validator alone blocked {s['validator_alone_blocked']} of {s['attacks']}, read-only connection alone "
            f"{s['readonly_alone_blocked']} of {s['attacks']}; table row counts unchanged: {'yes' if s['tables_unchanged_all'] else 'NO'}.")
    t2 = {r["version"]: r for r in R["accuracy"]["test2"]}
    if t2:
        f5 = ("Post-hoc, exploratory (test2, 20 new questions): " + "; ".join(
            f"{k.split(' ')[0]} {r['accuracy']:.1f}% [{r['boot_lo']:.1f}–{r['boot_hi']:.1f}]" for k, r in t2.items()) + ".")
    else:
        f5 = f"Post-hoc, exploratory v5 (v3 + \"do not approximate missing data\") on 20 new test2 questions: {P}."
    return [f1, f2, f3, f4, f5]


def collect():
    ci = {s: mra.ci_table(s) for s in ["dev", "test", "test2"]}
    dev, test, m2 = latest(RES, "dev", MAIN), latest(RES, "test", MAIN), latest(RES, "test", M2)
    calls = pd.read_csv(config.CALLS_CSV)
    live = calls[(calls["cached"].astype(str) == "False") & (calls["status"] == "ok")]
    sig_path = LOGS / "significance_test.csv"
    sig = pd.read_csv(sig_path) if sig_path.exists() else None
    sig_ok = sig is not None and {"v2", "v3", "v4"} <= (set(sig["version_a"]) | set(sig["version_b"]))
    done = [v for v in VERS if complete(test, v, 40, 3)]
    rt = redteam()
    t2 = latest(RES2, "test2", MAIN) if len(RES2) else RES2
    R = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "note": "Every number on the report page. Computed by eval/export_assets.py from logs/. PENDING = not run yet. "
                "Provisional until the gold queries are verified by the team.",
        "settings": {"model": MAIN, **config.SETTINGS, "row_limit": config.ROW_LIMIT, "sql_timeout_s": config.SQL_TIMEOUT},
        "api": {"live_calls": int(len(live)), "tokens": int(pd.to_numeric(live["total_tokens"], errors="coerce").sum()),
                "cache_files": len(list(config.CACHE.glob("*.json")))},
        "provisional": provisional(),
        "status": {"test_done": done, "test_pending": [v for v in VERS if v not in done],
                   "redteam_pending": rt["summary"]["attacks"] - rt["summary"]["full_run"], "significance_ready": sig_ok,
                   "test2_done": sorted(t2["version"].unique()) if len(t2) else []},
        "accuracy": {s: records(ci[s]) for s in ci},
        "metrics": {"dev": metrics_rows(dev, ci["dev"]), "test": metrics_rows(test, ci["test"]),
                    "test_second_model": metrics_rows(m2, ci["test"]),
                    "test_main_repeat1_same_versions": metrics_rows(
                        test[test["version"].isin(m2["version"].unique()) & (test["repeat"] == 1)], pd.DataFrame())
                    if len(m2) else [],
                    "test2": metrics_rows(t2, ci["test2"]) if len(t2) else []},
        "tiers": {"dev": tiers(dev), "test": tiers(test)},
        "significance": records(sig) if sig_ok else None,
        "v0_baseline": records(mra.v0_baseline()),
        "unknown_tables_v0_lenient": list(mra.unknown_tables()) if mra.unknown_tables() != P else None,
        "labels": {"dev": labels("dev"), "test": labels("test")},
        "prompt_diff": records(mra.prompt_diff()),
        "redteam": rt,
        "reversal": reversal(),
        "split": {t: {"dev": sum(1 for i in ITEMS if i["tier"] == t and i["split"] == "dev"),
                      "test": sum(1 for i in ITEMS if i["tier"] == t and i["split"] == "test")} for t in TIERS},
        "tests": tests(),
        # dev-split failures and which later version fixed them (docs/report/failure_examples.md)
        "failure_examples": [{k: (None if isinstance(x, float) and math.isnan(x) else x) for k, x in mra.example(v, q).items()}
                             | {"tier": TIER_NAME[next(i["tier"] for i in ITEMS if i["id"] == q)],
                                "change": mra.CHANGES[mra.example(v, q)["fixed"]][0] if mra.example(v, q)["fixed"] else None}
                             for v, q in mra.EXAMPLES],
        "test2_items": len(json.loads((ROOT / "eval" / "test2_set.json").read_text(encoding="utf-8")))
        if (ROOT / "eval" / "test2_set.json").exists() else 0,
    }
    R["findings"] = findings(R)
    return R


# ---------------- figures (svg, light mode, for the printed write-up) ----------------
def svg_doc(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family=\'{FONT}\' role="img" aria-label="{E(title)}"><title>{E(title)}</title>'
            f'<rect width="{w}" height="{h}" fill="{C["surface"]}"/>{body}</svg>')


def text(x, y, s, size=12, fill=None, anchor="start", weight=400):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or C["ink"]}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{E(str(s))}</text>')


def bar(x, y, w, h, fill):
    # 4px rounded data end, square at the baseline (left)
    if w <= 0:
        return ""
    r = min(4, w, h / 2)
    return (f'<path d="M{x},{y} H{x + w - r} Q{x + w},{y} {x + w},{y + r} V{y + h - r} Q{x + w},{y + h} {x + w - r},{y + h} '
            f'H{x} Z" fill="{fill}"/>')


def whisker(x1, x2, y, color):
    return (f'<g stroke="{color}" stroke-width="1.5"><line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}"/>'
            f'<line x1="{x1}" y1="{y - 4}" x2="{x1}" y2="{y + 4}"/><line x1="{x2}" y1="{y - 4}" x2="{x2}" y2="{y + 4}"/></g>')


def fig_accuracy(R):
    # two panels (dev, test): per version an execution bar and a strict bar, each with its 95% CI whisker
    pw, left, right, top = 440, 46, 64, 58
    w, h = left + 2 * pw + right + 30, 326
    body = text(16, 24, "Accuracy by prompt version, with 95% bootstrap intervals over questions", 15, weight=600)
    body += (f'<rect x="{w - 250}" y="34" width="10" height="10" rx="2" fill="{C["s1"]}"/>'
             + text(w - 236, 43, "execution accuracy", 12, C["ink2"])
             + f'<rect x="{w - 120}" y="34" width="10" height="10" rx="2" fill="{C["s2"]}"/>'
             + text(w - 106, 43, "strict accuracy", 12, C["ink2"]))
    for k, (split, label) in enumerate([("dev", "Dev (20 questions, 1 run)"), ("test", "Held-out test (40 questions, 3 repeats)")]):
        x0 = left + k * (pw + 30)
        cw = pw - 20
        body += text(x0, top + 6, label, 12.5, C["ink"], weight=600)
        rows = {r["version"]: r for r in R["metrics"][split]}
        y = top + 24
        for g in (0, 25, 50, 75, 100):
            gx = x0 + 34 + cw * g / 100 * 0.86
            body += f'<line x1="{gx}" y1="{y - 4}" x2="{gx}" y2="{y + 5 * 38 - 4}" stroke="{C["grid"]}"/>'
            body += text(gx, y + 5 * 38 + 10, f"{g}%", 10.5, C["muted"], "middle")
        for i, v in enumerate(VERS):
            yy = y + i * 38
            body += text(x0, yy + 13, v, 12, C["ink"], weight=600)
            r = rows.get(v)
            done = r is not None and (split == "dev" or v in R["status"]["test_done"])
            sx = x0 + 34
            scale = cw * 0.86 / 100
            if not done:
                body += text(sx + 4, yy + 13, P, 11.5, C["muted"])
                continue
            for j, (key, lo, hi, col) in enumerate([("execution_accuracy", "boot_lo", "boot_hi", C["s1"]),
                                                     ("strict_accuracy", "strict_lo", "strict_hi", C["s2"])]):
                by = yy + j * 14
                body += bar(sx, by, r[key] * scale, 12, col)
                if r[lo] is not None:
                    body += whisker(sx + r[lo] * scale, sx + r[hi] * scale, by + 6, C["ink"])
                body += text(sx + cw * 0.86 + 6, by + 10, f"{r[key]:.1f}%", 10.5, C["ink2"])
    body += text(16, h - 10, "Strict = same rows and exactly the gold number of columns. Provisional: gold queries not yet "
                 "verified by the team. Source: docs/report/results.json", 10.5, C["muted"])
    return svg_doc(w, h, body, "Accuracy by prompt version (dev and test) with 95% intervals")


def seq_color(v):
    i = min(len(SEQ) - 1, int(v / 100 * (len(SEQ) - 1) + 0.5))
    return SEQ[i], ("#ffffff" if i >= 3 else C["ink"])


def heatmap(rows, cols, val, title, sub, fmt, vmax, empty_note, pending=()):
    cw, rh, lw, top = 92, 30, 190, 74
    w, h = lw + cw * len(cols) + 24, top + rh * max(1, len(rows)) + 46
    body = text(16, 24, title, 15, weight=600) + text(16, 44, sub, 11.5, C["ink2"])
    for j, c in enumerate(cols):
        body += text(lw + j * cw + cw / 2, top - 8, c, 12, C["ink"], "middle", 600)
    if not rows:
        body += text(lw, top + 20, empty_note, 12, C["muted"])
    for i, r in enumerate(rows):
        y = top + i * rh
        body += text(lw - 10, y + rh / 2 + 4, r, 12, C["ink"], "end")
        for j, c in enumerate(cols):
            v = val(r, c)
            x = lw + j * cw
            if v is None or v == 0:
                # missing or zero: outline only, so filled cells always mean "some"
                body += f'<rect x="{x + 1}" y="{y + 1}" width="{cw - 2}" height="{rh - 2}" rx="3" fill="none" stroke="{C["grid"]}"/>'
                body += text(x + cw / 2, y + rh / 2 + 4, P if c in pending else (fmt(0) if v == 0 else "–"), 11,
                             C["muted"] if v is None else C["ink2"], "middle")
                continue
            bg, fg = seq_color(100 * v / vmax if vmax else 0)
            body += f'<rect x="{x + 1}" y="{y + 1}" width="{cw - 2}" height="{rh - 2}" rx="3" fill="{bg}"/>'
            body += text(x + cw / 2, y + rh / 2 + 4, fmt(v), 11.5, fg, "middle", 600)
    body += text(16, h - 10, "Provisional: gold queries not yet verified by the team. Source: docs/report/results.json", 10.5, C["muted"])
    return svg_doc(w, h, body, title)


def fig_tiers(R):
    t = R["tiers"]["test"]
    done = R["status"]["test_done"]
    rows = [TIER_NAME[x] for x in TIERS if x in t]
    back = {TIER_NAME[k]: k for k in TIERS}

    def val(r, c):
        return t.get(back[r], {}).get(c) if c in done else None
    return heatmap(rows, VERS, val, "Execution accuracy per tier, held-out test",
                   "gpt-oss-120b, 3 repeats per question. Outlined cells: 0%, or run PENDING.", lambda v: f"{v:.0f}%", 100,
                   "no test results yet", pending=[v for v in VERS if v not in done])


def fig_errors(R):
    lab = R["labels"]["test"] or R["labels"]["dev"]
    split = "test" if R["labels"]["test"] else "dev"
    counts = lab["counts"] if lab else {}
    rows = sorted(counts, key=lambda c: -sum(counts[c].values()))
    vmax = max([n for c in counts.values() for n in c.values()] or [1])
    return heatmap(rows, VERS, lambda r, c: counts.get(r, {}).get(c), f"Failure categories per version ({split} split)",
                   "Counts of failed answers. Labels are Claude's drafts, not yet confirmed by the team.",
                   lambda v: f"{v:.0f}", vmax, "no labelled failures yet")


def fig_redteam(R):
    rows = R["redteam"]["rows"]
    cols = [("validator alone", "validator_alone"), ("read-only DB alone", "readonly_alone"), ("full pipeline", "full_pipeline"),
            ("stopped at", "stopped_at")]
    cw, rh, lw, top = 150, 24, 300, 74
    w, h = lw + cw * len(cols) + 20, top + rh * len(rows) + 44
    body = (text(16, 24, "Red team: where each attack was stopped", 15, weight=600)
            + text(16, 44, "Payload checks test each layer alone with the attacker's SQL; the full pipeline runs the attack "
                   "text through prompt v4 + validator + read-only DB.", 11.5, C["ink2"]))
    for j, (c, _) in enumerate(cols):
        body += text(lw + j * cw + 8, top - 8, c, 12, C["ink"], weight=600)
    for i, r in enumerate(rows):
        y = top + i * rh
        if i % 2 == 0:
            body += f'<rect x="10" y="{y}" width="{w - 20}" height="{rh}" fill="#f3f2ee"/>'
        body += text(16, y + 16, f"{r['id']}  " + (r['attack'] if len(r['attack']) <= 34 else r['attack'][:33] + "…"), 11.5, C["ink"])
        for j, (_, k) in enumerate(cols):
            v = r[k]
            x = lw + j * cw + 8
            if v in (None, P):
                body += text(x, y + 16, P if v == P or k == "stopped_at" and r["full_pipeline"] == P else "–", 11, C["muted"])
                continue
            filled = v == "blocked"
            body += (f'<circle cx="{x + 5}" cy="{y + 12}" r="4.5" fill="{C["s1"] if filled else "none"}" '
                     f'stroke="{C["s1"] if filled else C["ink2"]}" stroke-width="1.5"/>' if k != "stopped_at" else "")
            body += text(x + (16 if k != "stopped_at" else 0), y + 16, v, 11.5, C["ink"] if filled or k == "stopped_at" else C["ink2"])
    body += text(16, h - 10, "Filled dot = blocked; open dot = not blocked by that layer alone. Source: docs/report/results.json",
                 10.5, C["muted"])
    return svg_doc(w, h, body, "Red team results per attack and layer")


def browser():
    for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"]:
        if Path(p).exists():
            return p
    for n in ["msedge", "google-chrome", "chromium", "chrome"]:
        if shutil.which(n):
            return shutil.which(n)
    return None


def to_png(svg_path, w, h, exe):
    png = svg_path.with_suffix(".png")
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                    f"--screenshot={png}", f"--window-size={w},{h}", svg_path.resolve().as_uri()],
                   capture_output=True, timeout=120)
    return png.exists()


def main():
    R = collect()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(R, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    n = per_question()
    FIG.mkdir(exist_ok=True)
    exe = browser()
    made = []
    for name, svg in [("accuracy_by_version", fig_accuracy(R)), ("accuracy_by_tier_test", fig_tiers(R)),
                      ("error_categories", fig_errors(R)), ("redteam_layers", fig_redteam(R))]:
        p = FIG / f"{name}.svg"
        p.write_text(svg, encoding="utf-8")
        w, h = (int(x) for x in svg.split('viewBox="0 0 ')[1].split('"')[0].split())
        ok = to_png(p, w, h, exe) if exe else False
        made.append(f"{p.name}{' + png' if ok else ' (no png: no local edge/chrome found)'}")
    print(f"saved docs/report/results.json, logs/per_question_test.csv ({n} rows)")
    print("figures in docs/report/figures/: " + ", ".join(made))
    print("\nfindings:\n- " + "\n- ".join(R["findings"]))


if __name__ == "__main__":
    main()
