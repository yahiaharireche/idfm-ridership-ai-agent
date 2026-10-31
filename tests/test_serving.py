# Automatic tests for the smaller copy of the database. It must give the agent
# exactly what the full database gives it. Skipped if the copy was not built:
#   .venv/bin/python model/build_serving.py
#
# Run from the project root:  .venv/bin/python -m pytest tests

import os

import duckdb
import pytest

from agent import tools

FULL = "data/model/ridership.duckdb"
SERVING = "data/model/ridership_serving.duckdb"

pytestmark = pytest.mark.skipif(not os.path.exists(SERVING), reason="the serving copy is not built")


@pytest.fixture(scope="module")
def full():
    connection = tools.connect(FULL)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def serving():
    connection = tools.connect(SERVING)
    yield connection
    connection.close()


def test_it_is_smaller():
    assert os.path.getsize(SERVING) < os.path.getsize(FULL) / 2


@pytest.mark.parametrize("table", [
    None, "dim_fare_category", "dim_line", "dim_station", "fact_rail_daily",
    "fact_rail_hourly", "fact_surface_daily", "fact_surface_hourly", "quality_flags",
])
def test_descriptions_are_the_same(full, serving, table):
    assert tools.describe_database(serving, table) == tools.describe_database(full, table)


@pytest.mark.parametrize("start, end", [
    ("2015-01-01", "2025-12-31"), ("2016-01-01", "2016-12-31"),
    ("2020-03-01", "2020-05-31"), ("2023-06-01", "2023-06-30"),
])
def test_warnings_are_the_same(full, serving, start, end):
    expected = tools.warnings_for_period(full, start, end)
    assert tools.warnings_for_period(serving, start, end) == expected
    assert "known problems" in expected.lower()


@pytest.mark.parametrize("sql", [
    "SELECT year, SUM(taps) FROM fact_rail_daily GROUP BY 1 ORDER BY 1",
    "SELECT year, SUM(taps) FROM fact_surface_daily GROUP BY 1 ORDER BY 1",
    "SELECT station_name, SUM(taps) AS n FROM fact_rail_daily WHERE year = 2023 GROUP BY 1 ORDER BY n DESC, 1 LIMIT 5",
    "SELECT * FROM fact_rail_hourly ORDER BY ALL LIMIT 20",
    "SELECT * FROM fact_surface_hourly ORDER BY ALL LIMIT 20",
    "SELECT * FROM warnings_between('2020-01-01', '2020-12-31') ORDER BY flag_id",
    "SELECT d.station_name, COUNT(*) FROM fact_rail_daily f JOIN dim_station d USING (station_key)"
    " GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3",
])
def test_queries_give_the_same_results(full, serving, sql):
    expected = tools.run_query(full, sql)
    assert not expected.startswith("Error")
    assert tools.run_query(serving, sql) == expected


@pytest.mark.parametrize("sql", [
    "DELETE FROM dim_line",
    "CREATE TABLE x AS SELECT 1",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SET lock_configuration = false",
])
def test_the_copy_is_locked_too(serving, sql):
    with pytest.raises(duckdb.Error):
        serving.sql(sql)
