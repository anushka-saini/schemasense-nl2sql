# one function, ask(), that sends a prompt to groq
# - every response is cached in logs/cache/ so reruns and the demo work without the api
# - every call (cached or not) is logged to logs/calls.csv
# - retries with backoff on rate limits

import csv
import hashlib
import json
import os
import time
from datetime import datetime

import groq
from dotenv import load_dotenv

import config

load_dotenv(config.ROOT / ".env")

FIELDS = [
    "time", "tag", "prompt_version", "question_id", "repeat", "model",
    "temperature", "top_p", "max_completion_tokens", "reasoning_effort",
    "prompt_tokens", "completion_tokens", "total_tokens", "latency_s",
    "cached", "finish_reason", "status", "error", "cache_key",
]

# errors worth retrying
RETRY_ERRORS = (groq.RateLimitError, groq.APIConnectionError,
                groq.APITimeoutError, groq.InternalServerError)

_client = None


def split_tag(tag):
    # tag looks like "v2:D03" -> ("v2", "D03"). anything else is kept as the version
    if ":" in tag:
        v, q = tag.split(":", 1)
        return v, q
    return tag, ""


def cache_key(prompt, repeat=1):
    # repeat is in the key so repeat runs (run 2, run 3) get their own fresh answer
    blob = json.dumps({"model": config.MODEL, "settings": config.SETTINGS,
                       "prompt": prompt, "repeat": repeat}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_cache(key):
    f = config.CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def is_cached(prompt, repeat=1):
    return load_cache(cache_key(prompt, repeat)) is not None


def log_call(row):
    config.LOGS.mkdir(exist_ok=True)
    new = not config.CALLS_CSV.exists()
    with open(config.CALLS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def get_client():
    global _client
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set. Put it in a .env file (see .env.example) "
                           "or use offline mode with cached answers.")
    if _client is None:
        _client = groq.Groq(api_key=key, max_retries=0)  # we do our own retries
    return _client


def call_api(prompt):
    client = get_client()
    for i in range(config.MAX_RETRIES + 1):
        try:
            t0 = time.time()
            r = client.chat.completions.create(
                model=config.MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=config.TEMPERATURE,
                top_p=config.TOP_P,
                max_completion_tokens=config.MAX_COMPLETION_TOKENS,
                reasoning_effort=config.REASONING_EFFORT,
            )
            return r, time.time() - t0
        except RETRY_ERRORS as e:
            if i == config.MAX_RETRIES:
                raise
            # use the server's retry-after if it sends one, else 2, 4, 8, 16, 32 s
            wait = 2 ** (i + 1)
            resp = getattr(e, "response", None)
            if resp is not None and resp.headers.get("retry-after"):
                try:
                    wait = float(resp.headers["retry-after"]) + 0.5
                except ValueError:
                    pass
            print(f"  {type(e).__name__}, retry {i + 1}/{config.MAX_RETRIES} in {wait:.1f}s")
            time.sleep(wait)


def ask(prompt, tag="", repeat=1, offline=False):
    """send prompt to the model (or read it from cache).
    tag is "version:question_id", e.g. "v2:D03". returns a dict."""
    version, qid = split_tag(tag)
    key = cache_key(prompt, repeat)
    row = {"time": datetime.now().isoformat(timespec="seconds"), "tag": tag,
           "prompt_version": version, "question_id": qid, "repeat": repeat,
           "model": config.MODEL, **config.SETTINGS, "cache_key": key}

    hit = load_cache(key)
    if hit:
        u = hit["usage"]
        log_call({**row, **u, "latency_s": 0, "cached": True,
                  "finish_reason": hit["finish_reason"], "status": "ok"})
        return {"text": hit["text"], "reasoning": hit.get("reasoning"), "cached": True,
                **u, "latency_s": hit["latency_s"], "finish_reason": hit["finish_reason"],
                "key": key}

    if offline:
        log_call({**row, "cached": False, "status": "error", "error": "offline: not in cache"})
        raise RuntimeError("Offline mode: this prompt is not in the cache.")

    try:
        r, secs = call_api(prompt)
    except Exception as e:
        log_call({**row, "cached": False, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]})
        raise

    msg = r.choices[0].message
    u = {"prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens,
         "total_tokens": r.usage.total_tokens}
    out = {"text": msg.content or "", "reasoning": getattr(msg, "reasoning", None),
           "finish_reason": r.choices[0].finish_reason}

    # save to cache with everything needed to trace it later
    config.CACHE.mkdir(parents=True, exist_ok=True)
    rec = {"key": key, "time": row["time"], "tag": tag, "repeat": repeat, "model": config.MODEL,
           "settings": config.SETTINGS, "prompt": prompt, **out, "usage": u,
           "latency_s": round(secs, 3)}
    (config.CACHE / f"{key}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
    log_call({**row, **u, "latency_s": round(secs, 3), "cached": False,
              "finish_reason": out["finish_reason"], "status": "ok"})

    time.sleep(config.SLEEP_BETWEEN_CALLS)
    return {**out, "cached": False, **u, "latency_s": round(secs, 3), "key": key}
