# The limits of the public app: how often one person can ask, and how much the
# whole app can spend in a day.
#
# The counts live in a small SQLite database (a single file), so restarting the
# app does not reset them. SQLite also lets several questions running at the
# same time count safely, one after the other.
#
# Money is reserved BEFORE a question runs, and the difference is given back
# after. Checking only what has already been spent is not enough: questions
# already running could all pass the limit together. (This is what made the
# stress test of Phase 6 overshoot its budget.)

import datetime
import os
import sqlite3
import time

from agent import settings

DATABASE = settings.LIMITS_DATABASE
QUESTIONS_PER_MINUTE = settings.QUESTIONS_PER_MINUTE
DAILY_SPENDING_LIMIT = settings.DAILY_SPENDING_LIMIT

# The most one question can cost: its limit is only checked after each step, so
# it can go over by the step that crosses it and by the final answer. The
# dearest step ever measured cost $0.01; this leaves room for $0.015 each.
RESERVED_PER_QUESTION = settings.MAX_COST_PER_QUESTION + 0.03

WINDOW_SECONDS = 60

TOO_MANY = "You are asking quite fast. Please wait a minute and try again."
DAY_IS_OVER = "The service has reached its spending limit for today. Please come back tomorrow."


def connect():
    folder = os.path.dirname(DATABASE)
    if folder:
        os.makedirs(folder, exist_ok=True)
    # isolation_level=None: transactions are started by hand, below.
    db = sqlite3.connect(DATABASE, timeout=10, isolation_level=None)
    db.execute("CREATE TABLE IF NOT EXISTS questions (ip TEXT, asked_at REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS spending (day TEXT PRIMARY KEY, spent REAL)")
    return db


def reserve(ip, now=None):
    """Asks for permission to run one question.

    Returns (reservation, None) if allowed, or (None, message for the user).
    A refused question is not counted against anyone.
    """
    now = now or time.time()
    day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date().isoformat()

    db = connect()
    try:
        # BEGIN IMMEDIATE: from here to COMMIT, no other question can read or
        # change the counts, so two of them cannot both take the last place.
        db.execute("BEGIN IMMEDIATE")

        # Addresses are personal data: each is forgotten after the minute it
        # is needed for.
        db.execute("DELETE FROM questions WHERE asked_at <= ?", [now - WINDOW_SECONDS])
        recent = db.execute("SELECT COUNT(*) FROM questions WHERE ip = ?", [ip]).fetchone()[0]
        if recent >= QUESTIONS_PER_MINUTE:
            db.execute("COMMIT")
            return None, TOO_MANY

        row = db.execute("SELECT spent FROM spending WHERE day = ?", [day]).fetchone()
        spent = row[0] if row else 0.0
        if spent + RESERVED_PER_QUESTION > DAILY_SPENDING_LIMIT:
            db.execute("COMMIT")
            return None, DAY_IS_OVER

        db.execute("INSERT INTO questions VALUES (?, ?)", [ip, now])
        db.execute("INSERT OR REPLACE INTO spending VALUES (?, ?)", [day, spent + RESERVED_PER_QUESTION])
        db.execute("COMMIT")
        return {"day": day, "amount": RESERVED_PER_QUESTION}, None
    finally:
        # Closing without COMMIT cancels the transaction if anything failed.
        db.close()


def settle(reservation, real_cost):
    """After a question: replaces what was reserved by what it really cost."""
    db = connect()
    try:
        db.execute("UPDATE spending SET spent = MAX(0, spent - ? + ?) WHERE day = ?",
                   [reservation["amount"], real_cost, reservation["day"]])
    finally:
        db.close()


def spent_on(day):
    """What is counted as spent on a day, written YYYY-MM-DD."""
    db = connect()
    try:
        row = db.execute("SELECT spent FROM spending WHERE day = ?", [day]).fetchone()
        return row[0] if row else 0.0
    finally:
        db.close()
