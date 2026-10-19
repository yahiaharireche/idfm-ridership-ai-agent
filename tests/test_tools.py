# Automatic tests for the agent's tools, agent/tools.py.
#
# Most of these are attacks: each one asks for something the agent must never
# be able to do, and checks that it is refused. If a change to tools.py ever
# weakens a safeguard, a test here fails.
#
# Run from the project root:  .venv/bin/python -m pytest tests

import duckdb
import pytest

from agent import tools


@pytest.fixture(scope="module")
def db():
    """One safe connection, shared by all the tests in this file."""
    connection = tools.connect()
    yield connection
    connection.close()


# --- the connection itself refuses everything except reading ---

@pytest.mark.parametrize("sql", [
    "DELETE FROM dim_line",
    "DROP TABLE dim_line",
    "CREATE TABLE x AS SELECT 1",
    "SELECT * FROM read_csv('/etc/passwd')",
    "ATTACH 'other.duckdb'",
    "INSTALL httpfs",
    "SET enable_external_access = true",
    "SET lock_configuration = false",
    "RESET memory_limit",
])
def test_connection_refuses(db, sql):
    # Sent straight to the connection, without run_query's checks, to prove
    # the connection is safe on its own.
    with pytest.raises(duckdb.Error):
        db.sql(sql)


# --- run_query ---

def test_query_returns_the_right_numbers(db):
    result = tools.run_query(db, "SELECT SUM(taps) FROM fact_rail_daily WHERE year = 2019")
    assert "1695721547" in result      # the 2019 total checked in test_model.py
    assert result.endswith("(1 rows)")


@pytest.mark.parametrize("sql", [
    "DELETE FROM dim_line",
    "UPDATE dim_line SET line_name = 'x'",
    "DROP TABLE dim_line",
    "EXPLAIN SELECT 1",
])
def test_query_refuses_anything_but_select(db, sql):
    assert tools.run_query(db, sql).startswith("Error: only SELECT")


def test_query_refuses_a_hidden_second_statement(db):
    result = tools.run_query(db, "SELECT 1; DROP TABLE dim_line")
    assert result.startswith("Error: send exactly one SQL statement")


def test_query_cannot_read_files(db):
    result = tools.run_query(db, "SELECT * FROM read_csv('/etc/passwd')")
    assert result.startswith("Error:")
    assert "root" not in result


def test_query_limits_rows_and_gives_the_total(db):
    result = tools.run_query(db, "SELECT line_key FROM dim_line")
    lines = result.splitlines()
    assert len(lines) == 1 + tools.MAX_ROWS + 1     # header, rows, note
    assert "of 2029 rows" in lines[-1]


def test_query_shows_empty_values_as_null(db):
    assert "NULL" in tools.run_query(db, "SELECT NULL AS taps")


def test_query_errors_help_the_model(db):
    # A misspelt column: DuckDB's message suggests the right one.
    result = tools.run_query(db, "SELECT station_nme FROM dim_station")
    assert result.startswith("Error:")
    assert "station_name" in result
    assert tools.run_query(db, "selec 1").startswith("Error: the SQL could not be read")


def test_query_is_stopped_after_the_time_limit(db, monkeypatch):
    # One second instead of ten, so the test is quick. A table joined with
    # itself is 20 million x 20 million rows: it would run for hours.
    monkeypatch.setattr(tools, "TIMEOUT_SECONDS", 1)
    result = tools.run_query(db, "SELECT COUNT(*) FROM fact_rail_daily a, fact_rail_daily b")
    assert result.startswith("Error: the query took more than 1 seconds")
    # and the connection still works afterwards
    assert "(1 rows)" in tools.run_query(db, "SELECT 1")


# --- describe_database ---

def test_describe_lists_every_table(db):
    result = tools.describe_database(db)
    for table in ["fact_rail_daily", "fact_surface_daily", "fact_rail_hourly",
                  "fact_surface_hourly", "dim_station", "dim_line",
                  "dim_fare_category", "quality_flags"]:
        assert table in result


def test_describe_gives_columns_with_meaning(db):
    result = tools.describe_database(db, "fact_rail_daily")
    assert "- taps (" in result
    assert "unknown, not zero" in result        # the description, not just the name


@pytest.mark.parametrize("table", ["stations", "x' OR 1=1 --"])
def test_describe_unknown_table_lists_the_real_ones(db, table):
    result = tools.describe_database(db, table)
    assert result.startswith("Error: there is no table called")
    assert "fact_rail_daily" in result


# --- warnings_for_period ---

def test_warnings_cover_the_period(db):
    result = tools.warnings_for_period(db, "2016-01-01", "2016-12-31")
    assert "suppressed_counts_rail" in result   # hidden counts, 2015 to mid-2017
    assert "unknown_line_2015" in result


def test_warnings_leave_out_other_periods(db):
    result = tools.warnings_for_period(db, "2024-01-01", "2024-12-31")
    assert "suppressed_counts_rail" not in result


@pytest.mark.parametrize("start, end, message", [
    ("2020-13-01", "2020-04-01", "must be real dates"),
    ("2020-05-01", "2020-04-01", "is after end"),
    ("2020-01-01'); DROP TABLE x --", "2020-02-01", "must be real dates"),
    ("2030-01-01", "2030-02-01", "there is no data"),
])
def test_warnings_refuse_bad_dates(db, start, end, message):
    result = tools.warnings_for_period(db, start, end)
    assert result.startswith("Error:")
    assert message in result


# --- after every attack above, the data is untouched ---

def test_database_unchanged(db):
    # pytest runs the tests in the order they are written, so this one is last.
    assert tools.run_query(db, "SELECT COUNT(*) FROM dim_line").startswith("count_star()\n2029")
