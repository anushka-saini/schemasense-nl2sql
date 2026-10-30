# all model and run settings live here, every experiment uses these same values

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "olist.db"
PROMPTS = ROOT / "prompts"
LOGS = ROOT / "logs"
CACHE = LOGS / "cache"
CALLS_CSV = LOGS / "calls.csv"

# model settings (groq)
MODEL = "openai/gpt-oss-120b"
TEMPERATURE = 0
TOP_P = 1
MAX_COMPLETION_TOKENS = 2048
REASONING_EFFORT = "medium"

# these go into the cache key and into every log row
SETTINGS = {
    "temperature": TEMPERATURE,
    "top_p": TOP_P,
    "max_completion_tokens": MAX_COMPLETION_TOKENS,
    "reasoning_effort": REASONING_EFFORT,
}

# api politeness
SLEEP_BETWEEN_CALLS = 1.0  # seconds after every real api call
MAX_RETRIES = 5            # on rate limit / connection errors

# sql execution
ROW_LIMIT = 200
SQL_TIMEOUT = 10  # seconds
