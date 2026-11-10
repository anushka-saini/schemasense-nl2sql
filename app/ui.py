# small html helpers + the css for the app (cards, status badges, steps)

from html import escape

# prompt version -> fixed chart colour (same order as chartCategoricalColors in .streamlit/config.toml)
VERSION_COLORS = {"v0": "#3987e5", "v1": "#d95926", "v2": "#199e70", "v3": "#c98500", "v4": "#d55181", "v5": "#008300"}

CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1400px;}
.hero h1 {font-size: 1.85rem; margin: 0; letter-spacing: -0.01em;}
.hero p {color: #9aa3ad; margin: .2rem 0 1rem 0;}
.card {background: #171b21; border: 1px solid #262c35; border-radius: 10px; padding: .75rem 1rem; height: 100%;}
.card .label {color: #9aa3ad; font-size: .74rem; text-transform: uppercase; letter-spacing: .05em;}
.card .value {font-size: 1.45rem; font-weight: 600; margin-top: .1rem;}
.card .sub {color: #9aa3ad; font-size: .8rem; margin-top: .15rem;}
.step {background: #171b21; border: 1px solid #262c35; border-radius: 10px; padding: .6rem .7rem; min-height: 96px;}
.step.fail {border-color: #d03b3b;}
.step.warn {border-color: #fab219;}
.step .name {font-weight: 600; font-size: .88rem; margin-bottom: .35rem;}
.step .detail {color: #9aa3ad; font-size: .76rem; margin-top: .35rem; word-break: break-word;}
.badge {display: inline-block; padding: .1rem .55rem; border-radius: 999px; font-size: .74rem; font-weight: 600;}
.badge.ok {background: rgba(12,163,12,.14); color: #5fd35f; border: 1px solid rgba(12,163,12,.55);}
.badge.warn {background: rgba(250,178,25,.12); color: #fab219; border: 1px solid rgba(250,178,25,.55);}
.badge.fail {background: rgba(208,59,59,.15); color: #ff8080; border: 1px solid rgba(208,59,59,.65);}
.badge.skip {background: rgba(140,150,160,.10); color: #9aa3ad; border: 1px solid #39414b;}
.badge.info {background: rgba(57,135,229,.14); color: #8ab8f2; border: 1px solid rgba(57,135,229,.55);}
.callout {border-radius: 10px; padding: .75rem 1rem; margin: .7rem 0;}
.callout.fail {background: rgba(208,59,59,.10); border: 1px solid rgba(208,59,59,.6);}
.callout.warn {background: rgba(250,178,25,.08); border: 1px solid rgba(250,178,25,.5);}
.callout.info {background: rgba(57,135,229,.08); border: 1px solid rgba(57,135,229,.45);}
.callout b {display: block; margin-bottom: .15rem;}
.muted {color: #9aa3ad; font-size: .85rem;}
.dot {display: inline-block; width: .7rem; height: .7rem; border-radius: 50%; margin-right: .4rem;}
.empty {border: 1px dashed #39414b; border-radius: 10px; padding: 1.3rem 1.4rem; color: #9aa3ad; margin: .5rem 0 .8rem 0;}
.empty b {color: #e6e8eb; font-size: 1.05rem; display: block; margin-bottom: .25rem;}
.side-k {color: #9aa3ad; font-size: .74rem; text-transform: uppercase; letter-spacing: .05em; margin-top: .55rem;}
.side-v {font-weight: 600; font-size: .92rem;}
</style>
"""

# state -> (css class, icon + word), so meaning never depends on colour alone
STATE = {"ok": ("ok", "✓ OK"), "warn": ("warn", "! Warning"), "fail": ("fail", "✕ Failed"),
         "skip": ("skip", "– Skipped"), "info": ("info", "i Info")}

# pipeline status -> badge state + label
STATUS = {"ok": ("ok", "✓ Answered"), "empty": ("warn", "! No rows"), "refused": ("warn", "! CANNOT_ANSWER"),
          "blocked": ("fail", "✕ Blocked"), "invalid": ("fail", "✕ Rejected"), "error": ("fail", "✕ SQL error"),
          "llm_error": ("fail", "✕ LLM call failed")}


def badge(state, text=None):
    cls, default = STATE.get(state, STATE["skip"])
    return f'<span class="badge {cls}">{escape(text) if text else default}</span>'


def status_badge(status):
    cls, text = STATUS.get(status, ("skip", status))
    return f'<span class="badge {cls}">{escape(text)}</span>'


def step_card(step):
    st_ = step["state"]
    return (f'<div class="step {st_}"><div class="name">{escape(step["name"])}</div>{badge(st_)}'
            f'<div class="detail">{escape(str(step["detail"])[:220])}</div></div>')


def card(label, value, sub=""):
    return (f'<div class="card"><div class="label">{escape(label)}</div>'
            f'<div class="value">{escape(str(value))}</div><div class="sub">{escape(sub)}</div></div>')


def callout(kind, title, body=""):
    return f'<div class="callout {kind}"><b>{escape(title)}</b>{escape(body)}</div>'


def empty(title, body):
    return f'<div class="empty"><b>{escape(title)}</b>{escape(body)}</div>'


def side(k, v):
    return f'<div class="side-k">{escape(k)}</div><div class="side-v">{escape(str(v))}</div>'


def dot(version):
    return f'<span class="dot" style="background:{VERSION_COLORS.get(version, "#9aa3ad")}"></span>'
