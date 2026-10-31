# Everything that can differ between this computer and a server, in one place.
#
# Each setting comes from an environment variable (a value given to a program
# when it starts), and falls back to the default that works on this computer.
# So the same code runs everywhere, and changing a limit never means editing
# the code. For example:
#   AGENT_DAILY_SPENDING_LIMIT=1 .venv/bin/python app.py
#
# The API key is not here: it is read in agent/llm.py.

import math
import os


def text(name, default):
    """A text setting. An empty variable counts as not set."""
    return os.environ.get(name) or default


def number(name, default, kind=float):
    """A number setting, which must be above zero.

    A wrong value stops the program at once. Silently accepting "nan" here
    would switch off a spending limit, because no amount is ever above nan.
    """
    value = os.environ.get(name)
    if not value:
        return default
    try:
        value = kind(value)
    except ValueError:
        raise SystemExit(name + " must be a number, got '" + value + "'.")
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(name + " must be a number above zero, got '" + str(value) + "'.")
    return value


def yes_no(name, default):
    """An on/off setting, written 1 or 0."""
    value = os.environ.get(name)
    if not value:
        return default
    if value not in ("0", "1"):
        raise SystemExit(name + " must be 1 or 0, got '" + value + "'.")
    return value == "1"


# --- the data ---

DATABASE = text("AGENT_DATABASE", "data/model/ridership.duckdb")
MEMORY_LIMIT = text("AGENT_MEMORY_LIMIT", "4GB")

# --- one question ---

MAX_STEPS = number("AGENT_MAX_STEPS", 10, int)
MAX_TOKENS = number("AGENT_MAX_TOKENS", 4000, int)
MAX_QUESTION_LENGTH = number("AGENT_MAX_QUESTION_LENGTH", 1000, int)

# Past questions cost $0.003 (median) to $0.03 (the most expensive 5%), and the
# dearest single one $0.05. So this limit only stops questions going wrong.
MAX_COST_PER_QUESTION = number("AGENT_MAX_COST_PER_QUESTION", 0.05)

RUNS = text("AGENT_RUNS_FOLDER", "runs")

# --- the public app ---

QUESTIONS_PER_MINUTE = number("AGENT_QUESTIONS_PER_MINUTE", 5, int)
DAILY_SPENDING_LIMIT = number("AGENT_DAILY_SPENDING_LIMIT", 0.50)
LIMITS_DATABASE = text("AGENT_LIMITS_DATABASE", "data/app/limits.sqlite")
CONCURRENT_QUESTIONS = number("AGENT_CONCURRENT_QUESTIONS", 3, int)

# Only safe to turn on when a proxy we control stands in front of the app (see
# client_ip in app.py). Otherwise anyone could pretend to be someone else.
BEHIND_PROXY = yes_no("AGENT_BEHIND_PROXY", False)

HOST = text("AGENT_HOST", "127.0.0.1")     # this computer only, by default
PORT = number("AGENT_PORT", 7860, int)
