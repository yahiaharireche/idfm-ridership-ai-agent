# Automatic tests for the web page's logic, app.py. Free: the fake model of
# test_agent.py plays the model's part.
#
# Run from the project root:  .venv/bin/python -m pytest tests

import types

import pytest

import app
from agent import agent, limits, settings, tools
from tests.test_agent import BrokenClient, FakeClient, reply

QUERY = '{"sql": "SELECT 41 + 1 AS n"}'
BAD_QUERY = '{"sql": "SELECT nothing_here"}'
WARNINGS = '{"start": "2016-01-01", "end": "2016-12-31"}'


@pytest.fixture(scope="module")
def db():
    connection = tools.connect()
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "RUNS", str(tmp_path / "runs"))
    monkeypatch.setattr(limits, "DATABASE", str(tmp_path / "limits.sqlite"))
    monkeypatch.setattr(limits, "QUESTIONS_PER_MINUTE", 3)
    monkeypatch.setattr(limits, "DAILY_SPENDING_LIMIT", 0.50)
    monkeypatch.setattr(limits, "RESERVED_PER_QUESTION", 0.10)


def today_spent():
    db = limits.connect()
    row = db.execute("SELECT SUM(spent) FROM spending").fetchone()
    db.close()
    return row[0] or 0.0


def test_the_page_shows_the_evidence(db):
    client = FakeClient([
        reply(calls=[("a", "run_query", QUERY), ("b", "warnings_for_period", WARNINGS)]),
        reply("The answer is 42."),
    ])
    answer, details, sql, warnings, steps = app.respond("question", "1.1.1.1", client, db)
    assert answer == "The answer is 42."
    assert "2 steps" in details and "$0.0020" in details
    assert "SELECT 41 + 1 AS n" in sql
    assert "known problems" in warnings
    assert "run_query" in steps and "Wrote the answer." in steps


def test_a_failed_query_is_marked(db):
    client = FakeClient([reply(calls=[("a", "run_query", BAD_QUERY)]), reply("Sorry.")])
    sql = app.respond("question", "1.1.1.1", client, db)[2]
    assert "failed" in sql


def test_warnings_read_with_a_query_count(db):
    query = '{"sql": "SELECT flag_id FROM warnings_between(\'2016-01-01\', \'2016-12-31\')"}'
    client = FakeClient([reply(calls=[("a", "run_query", query)]), reply("ok")])
    warnings = app.respond("question", "1.1.1.1", client, db)[3]
    assert "did not check" not in warnings


def test_the_page_says_when_warnings_were_not_checked(db):
    warnings = app.respond("question", "1.1.1.1", FakeClient([reply("ok")]), db)[3]
    assert "did not check" in warnings


def test_broken_arguments_do_not_break_the_page(db):
    client = FakeClient([reply(calls=[("a", "run_query", '{"sql": ')]), reply("ok")])
    assert app.respond("question", "1.1.1.1", client, db)[0] == "ok"


def test_long_results_are_cut_in_the_steps(db):
    query = '{"sql": "SELECT repeat(\'x\', 5000) AS long"}'
    client = FakeClient([reply(calls=[("a", "run_query", query)]), reply("ok")])
    assert "cut here" in app.respond("question", "1.1.1.1", client, db)[4]


def test_an_early_stop_is_shown(db):
    expensive = reply(calls=[("a", "run_query", QUERY)])
    expensive["usage"]["cost"] = 0.06
    client = FakeClient([expensive, reply("What I have.")])
    assert "cost limit" in app.respond("question", "1.1.1.1", client, db)[1]


def test_an_empty_question_uses_no_turn_and_no_money(db):
    client = FakeClient([reply("no")])
    assert app.respond("  ", "1.1.1.1", client, db)[0] == "Please type a question."
    assert client.sent == []
    assert today_spent() == 0


def test_the_real_cost_is_what_stays_counted(db):
    app.respond("question", "1.1.1.1", FakeClient([reply("ok")]), db)
    assert today_spent() == pytest.approx(0.001)


def test_the_person_limit_reaches_the_page(db):
    client = FakeClient([reply("ok")])
    for _ in range(3):
        app.respond("question", "1.1.1.1", client, db)
    calls = len(client.sent)
    assert app.respond("question", "1.1.1.1", client, db)[0] == limits.TOO_MANY
    assert len(client.sent) == calls    # the model was not called


def test_the_daily_limit_reaches_the_page(db, monkeypatch):
    monkeypatch.setattr(limits, "DAILY_SPENDING_LIMIT", 0.05)
    client = FakeClient([reply("ok")])
    assert app.respond("question", "1.1.1.1", client, db)[0] == limits.DAY_IS_OVER
    assert client.sent == []


def test_a_service_failure_is_clear_and_gives_the_money_back(db):
    answer = app.respond("question", "1.1.1.1", BrokenClient([]), db)[0]
    assert answer == agent.UNAVAILABLE
    assert today_spent() == 0


def test_a_crash_keeps_the_reservation(db, monkeypatch, capsys):
    # What the question spent is unknown, so its reservation stays counted.
    def crash(*args):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(agent, "ask", crash)
    answer = app.respond("question", "1.1.1.1", FakeClient([reply("no")]), db)[0]
    assert answer == app.UNEXPECTED
    assert "secret detail" not in answer
    assert today_spent() == pytest.approx(0.10)


def test_if_limits_cannot_be_checked_nothing_runs(db, monkeypatch, capsys):
    def crash(*args):
        raise OSError("disk full")
    monkeypatch.setattr(limits, "connect", crash)
    client = FakeClient([reply("no")])
    assert app.respond("question", "1.1.1.1", client, db)[0] == app.UNEXPECTED
    assert client.sent == []


# --- who is asking ---

def request(host, forwarded=None):
    headers = {"x-forwarded-for": forwarded} if forwarded else {}
    return types.SimpleNamespace(client=types.SimpleNamespace(host=host), headers=headers)


def test_without_a_proxy_the_header_is_ignored(monkeypatch):
    # Anyone can write this header, so it must not decide who they are.
    monkeypatch.setattr(settings, "BEHIND_PROXY", False)
    assert app.client_ip(request("1.1.1.1", "9.9.9.9")) == "1.1.1.1"


def test_behind_a_proxy_the_last_address_counts(monkeypatch):
    # The last one is what our proxy added. Those before it came from the visitor.
    monkeypatch.setattr(settings, "BEHIND_PROXY", True)
    assert app.client_ip(request("10.0.0.1", "9.9.9.9, 5.5.5.5")) == "5.5.5.5"


def test_the_page_can_be_built():
    assert app.build_page() is not None
