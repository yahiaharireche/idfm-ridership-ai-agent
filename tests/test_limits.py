# Automatic tests for the public app's limits, agent/limits.py, and for the
# settings read from the environment. Free: no model is called.
#
# Run from the project root:  .venv/bin/python -m pytest tests

import concurrent.futures

import pytest

from agent import limits, llm, settings

NOW = 1_800_000_000.0    # a fixed moment, so the tests do not depend on the clock
DAY = "2027-01-15"


@pytest.fixture(autouse=True)
def fresh_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "DATABASE", str(tmp_path / "limits.sqlite"))
    monkeypatch.setattr(limits, "QUESTIONS_PER_MINUTE", 3)
    monkeypatch.setattr(limits, "DAILY_SPENDING_LIMIT", 0.50)
    monkeypatch.setattr(limits, "RESERVED_PER_QUESTION", 0.10)


# --- per person ---

def test_one_person_is_stopped_after_the_limit():
    for _ in range(3):
        assert limits.reserve("1.1.1.1", NOW)[1] is None
    assert limits.reserve("1.1.1.1", NOW) == (None, limits.TOO_MANY)


def test_another_person_is_not_affected():
    for _ in range(3):
        limits.reserve("1.1.1.1", NOW)
    assert limits.reserve("2.2.2.2", NOW)[1] is None


def test_the_limit_ends_after_a_minute():
    for _ in range(3):
        limits.reserve("1.1.1.1", NOW)
    assert limits.reserve("1.1.1.1", NOW + 59)[1] == limits.TOO_MANY
    assert limits.reserve("1.1.1.1", NOW + 60)[1] is None


def test_addresses_are_forgotten():
    limits.reserve("1.1.1.1", NOW)
    limits.reserve("2.2.2.2", NOW + 61)
    db = limits.connect()
    assert db.execute("SELECT ip FROM questions").fetchall() == [("2.2.2.2",)]
    db.close()


# --- per day ---

def test_money_is_reserved_before_the_question_runs():
    limits.reserve("1.1.1.1", NOW)
    assert limits.spent_on(DAY) == pytest.approx(0.10)


def test_settling_counts_the_real_cost():
    reservation, _ = limits.reserve("1.1.1.1", NOW)
    limits.settle(reservation, 0.003)
    assert limits.spent_on(DAY) == pytest.approx(0.003)


def test_the_app_stops_at_the_daily_limit():
    for second in range(5):
        assert limits.reserve("ip" + str(second), NOW + second)[1] is None
    assert limits.reserve("another", NOW + 5) == (None, limits.DAY_IS_OVER)


def test_settling_gives_room_back():
    reservations = []
    for second in range(5):
        reservations.append(limits.reserve("ip" + str(second), NOW + second)[0])
    limits.settle(reservations[0], 0.0)
    assert limits.reserve("another", NOW + 5)[1] is None


def test_a_new_day_starts_at_zero():
    for second in range(5):
        limits.reserve("ip" + str(second), NOW + second)
    assert limits.reserve("another", NOW + 86400)[1] is None


def test_refused_by_the_daily_limit_does_not_use_up_a_persons_turns():
    for second in range(5):
        limits.reserve("ip" + str(second), NOW + second)
    for _ in range(5):
        assert limits.reserve("same", NOW + 5)[1] == limits.DAY_IS_OVER


def test_questions_at_the_same_time_cannot_pass_the_limit_together():
    # 20 at once, room for 5: without the transaction, several would read the
    # same total and all pass.
    def one(number):
        return limits.reserve("ip" + str(number), NOW)[0]

    with concurrent.futures.ThreadPoolExecutor(20) as pool:
        results = list(pool.map(one, range(20)))
    assert len([r for r in results if r]) == 5
    assert limits.spent_on(DAY) == pytest.approx(0.50)


def test_counts_survive_a_restart():
    # Each call opens the file anew, as a restarted app would.
    for _ in range(3):
        limits.reserve("1.1.1.1", NOW)
    limits.connect().close()
    assert limits.reserve("1.1.1.1", NOW)[1] == limits.TOO_MANY


# --- settings ---

def test_number_uses_the_default_when_not_set(monkeypatch):
    monkeypatch.delenv("TEST_SETTING", raising=False)
    assert settings.number("TEST_SETTING", 5) == 5


def test_number_reads_the_environment(monkeypatch):
    monkeypatch.setenv("TEST_SETTING", "0.25")
    assert settings.number("TEST_SETTING", 5) == 0.25


@pytest.mark.parametrize("value", ["abc", "nan", "inf", "0", "-1"])
def test_a_bad_limit_stops_the_program(monkeypatch, value):
    # "nan" matters most: no amount is ever above nan, so it would switch a
    # spending limit off without a word.
    monkeypatch.setenv("TEST_SETTING", value)
    with pytest.raises(SystemExit):
        settings.number("TEST_SETTING", 5)


def test_the_key_from_the_environment_comes_first(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-environment")
    monkeypatch.setattr(llm, "KEY_FILE", "this-file-does-not-exist")
    assert llm.read_key() == "from-environment"


def test_the_key_falls_back_to_the_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    key_file = tmp_path / "keys"
    key_file.write_text("OPENROUTER_API_KEY=from-file\n")
    monkeypatch.setattr(llm, "KEY_FILE", str(key_file))
    assert llm.read_key() == "from-file"


def test_no_key_anywhere_is_a_clear_stop(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(llm, "KEY_FILE", "this-file-does-not-exist")
    with pytest.raises(SystemExit):
        llm.read_key()
